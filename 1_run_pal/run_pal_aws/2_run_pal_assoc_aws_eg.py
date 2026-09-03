#!/usr/bin/env python3
"""Submit one yearly multi-subnetwork PAL association job."""

import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from sagemaker.core.helper.session_helper import Session, get_execution_role
from sagemaker.core.image_uris import retrieve
from sagemaker.core.processing import ScriptProcessor
from sagemaker.core.shapes import (
    ProcessingInput,
    ProcessingOutput,
    ProcessingS3Input,
    ProcessingS3Output,
)

from processing_job.job_common import prefix_has_objects, upload_tree


# ============================================================================
# USER SETTINGS: CASE, INPUTS, DATES, AND PAL EXECUTION
# ============================================================================
PALM_ROOT = Path("~/shared/software/PALM").expanduser()
CASE_CODE = "eg"
subnet_station_files = {
    "r1": "station_scedc_aws_selected_r1_final.csv",
    "r2": "station_scedc_aws_selected_r2_final.csv",
    "r3": "station_scedc_aws_selected_r3_final.csv",
    "r4": "station_scedc_aws_selected_r4_final.csv",
    "r5": "station_scedc_aws_selected_r5_final.csv",
    "r6": "station_scedc_aws_selected_r6_final.csv",
}
time_range = "20200101-20210101"  # Exclusive end date.
study_year = 2020

num_workers = 6
overwrite = False
retry_failed_days = True
association_buffer_enabled = False

# ============================================================================
# USER SETTINGS: AWS RESOURCES, PICK INPUTS, AND RESUME BEHAVIOR
# ============================================================================
region = "us-west-2"
results_s3_prefix = "sagemaker/scsn-pal/results"
resume_existing_output = True

primary_pick_job_code = "%s-pick-%d" % (CASE_CODE, study_year)
boundary_pick_objects = ()

instance_type = "ml.t3.2xlarge"
instance_count = 1
threads_per_worker = 1
volume_size_gb = 50
max_runtime_seconds = 432000

framework_version = "1.4-2"
python_version = "py3"


# ============================================================================
# CONNECTION CODE: normally no edits are needed below this line
# ============================================================================
workflow_dir = Path(__file__).resolve().parent
processing_dir = workflow_dir / "processing_job"
pal_dir = PALM_ROOT / "PAL_src"
case_config_file = "config_aws_%s.py" % CASE_CODE
job_code = "%s-assoc-%d" % (CASE_CODE, study_year)
pal_files = (
    "associator_pal.py",
    "association_runner.py",
    "data_pipeline_aws.py",
    "phase_merge.py",
    "pick_ensemble.py",
    "trigger_counts.py",
)


def dates_in_range(value):
    start_text, end_text = value.split("-")
    start = datetime.strptime(start_text, "%Y%m%d").date()
    end = datetime.strptime(end_text, "%Y%m%d").date()
    if start >= end:
        raise ValueError("time_range must have start < exclusive end")
    return [
        start + timedelta(days=index)
        for index in range((end - start).days)
    ]


def s3_filenames(s3, bucket, prefix, suffix):
    filenames = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith(suffix):
                filenames.add(key.rsplit("/", 1)[-1])
    return filenames


def processing_input(name, uri, local_path):
    return ProcessingInput(
        input_name=name,
        s3_input=ProcessingS3Input(
            s3_uri=uri,
            local_path=local_path,
            s3_data_type="S3Prefix",
            s3_input_mode="File",
        ),
    )


def main():
    expected_range = "%d0101-%d0101" % (study_year, study_year + 1)
    if time_range != expected_range:
        raise ValueError(
            "time_range {} does not match study_year {}; expected {}".format(
                time_range, study_year, expected_range
            )
        )

    local_config = workflow_dir / case_config_file
    entry = processing_dir / "processing_entry_assoc.py"
    requirements = processing_dir / "requirements.txt"
    required = [local_config, entry, requirements]
    required.extend(pal_dir / name for name in pal_files)
    for filename in subnet_station_files.values():
        required.append(workflow_dir / "input" / filename)
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)

    session = Session(boto_session=boto3.Session(region_name=region))
    role = get_execution_role()
    bucket = session.default_bucket()
    s3 = boto3.client("s3", region_name=region)

    primary_pick_prefix = (
        results_s3_prefix + "/" + primary_pick_job_code
        + "/output/" + CASE_CODE + "/picks/"
    )
    expected_pick_names = {
        "{}.pick".format(value) for value in dates_in_range(time_range)
    }
    available_pick_names = s3_filenames(
        s3, bucket, primary_pick_prefix, ".pick"
    )
    missing = sorted(expected_pick_names - available_pick_names)
    if missing:
        preview = ", ".join(missing[:10])
        if len(missing) > 10:
            preview += ", ..."
        raise FileNotFoundError(
            "{} target pick files are missing under s3://{}/{}: {}".format(
                len(missing), bucket, primary_pick_prefix, preview
            )
        )
    expected_trigger_names = {
        "{}.trigger_counts.csv".format(value)
        for value in dates_in_range(time_range)
    }
    available_trigger_names = s3_filenames(
        s3, bucket, primary_pick_prefix, ".trigger_counts.csv"
    )
    missing = sorted(expected_trigger_names - available_trigger_names)
    if missing:
        preview = ", ".join(missing[:10])
        if len(missing) > 10:
            preview += ", ..."
        raise FileNotFoundError(
            "{} STA/LTA trigger inventories are missing under "
            "s3://{}/{}: {}. Re-run picking with the current source."
            .format(
                len(missing), bucket, primary_pick_prefix, preview
            )
        )

    pick_prefixes = [primary_pick_prefix]
    pick_prefixes.extend(
        results_s3_prefix + "/" + code + "/output/" + CASE_CODE
        + "/picks/" + filename
        for code, filename in boundary_pick_objects
    )
    for prefix in pick_prefixes:
        if not prefix_has_objects(s3, bucket, prefix):
            raise FileNotFoundError(
                "no pick objects at s3://{}/{}".format(bucket, prefix)
            )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job_name = job_code + "-" + timestamp
    stage_root = "sagemaker/scsn-pal/jobs/" + job_name
    source_prefix = stage_root + "/source"
    work_prefix = stage_root + "/work"
    output_prefix = (
        results_s3_prefix + "/" + job_code + "/output/assoc"
    )
    output_uri = "s3://{}/{}/".format(bucket, output_prefix)

    runtime = {
        "case_code": CASE_CODE,
        "subnet_station_files": {
            name: "input/" + filename
            for name, filename in subnet_station_files.items()
        },
        "pick_dir": "output/%s/picks" % CASE_CODE,
        "out_root": "output/%s_assoc" % CASE_CODE,
        "time_range": time_range,
        "num_workers": num_workers,
        "overwrite": overwrite,
        "retry_failed_days": retry_failed_days,
        "association_buffer_enabled": association_buffer_enabled,
    }

    with tempfile.TemporaryDirectory(prefix="pal-assoc-") as temp_dir:
        stage = Path(temp_dir)
        source_stage = stage / "source"
        work_stage = stage / "work"
        (work_stage / "input").mkdir(parents=True)
        source_stage.mkdir(parents=True)

        shutil.copy2(local_config, work_stage / case_config_file)
        shutil.copy2(requirements, work_stage / "requirements.txt")
        (work_stage / "assoc_runtime.json").write_text(
            json.dumps(runtime, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for filename in subnet_station_files.values():
            shutil.copy2(
                workflow_dir / "input" / filename,
                work_stage / "input" / filename,
            )
        for name in pal_files:
            shutil.copy2(pal_dir / name, source_stage / name)

        upload_tree(s3, source_stage, bucket, source_prefix)
        upload_tree(s3, work_stage, bucket, work_prefix)

    inputs = [
        processing_input(
            "source", "s3://{}/{}/".format(bucket, source_prefix),
            "/opt/ml/processing/pal",
        ),
        processing_input(
            "work", "s3://{}/{}/".format(bucket, work_prefix),
            "/opt/ml/processing/work",
        ),
    ]
    for index, prefix in enumerate(pick_prefixes):
        inputs.append(
            processing_input(
                "picks-{}".format(index),
                "s3://{}/{}".format(bucket, prefix),
                "/opt/ml/processing/picks/{}".format(index),
            )
        )

    output_exists = prefix_has_objects(s3, bucket, output_prefix)
    if output_exists and not resume_existing_output:
        raise FileExistsError(
            "output exists at {}; enable resume or use a new job code".format(
                output_uri
            )
        )
    if output_exists:
        inputs.append(
            processing_input(
                "resume-output", output_uri,
                "/opt/ml/processing/resume",
            )
        )
        print("resuming from: " + output_uri)

    image_uri = retrieve(
        framework="sklearn",
        region=region,
        version=framework_version,
        py_version=python_version,
        instance_type=instance_type,
    )
    processor = ScriptProcessor(
        image_uri=image_uri,
        command=["python3"],
        role=role,
        instance_type=instance_type,
        instance_count=instance_count,
        volume_size_in_gb=volume_size_gb,
        max_runtime_in_seconds=max_runtime_seconds,
        base_job_name=job_code,
        sagemaker_session=session,
        env={
            "OMP_NUM_THREADS": str(threads_per_worker),
            "OPENBLAS_NUM_THREADS": str(threads_per_worker),
            "MKL_NUM_THREADS": str(threads_per_worker),
            "NUMEXPR_NUM_THREADS": str(threads_per_worker),
            "BLIS_NUM_THREADS": str(threads_per_worker),
            "OMP_DYNAMIC": "FALSE",
        },
    )
    processor.run(
        code=str(entry),
        inputs=inputs,
        outputs=[
            ProcessingOutput(
                output_name="association",
                s3_output=ProcessingS3Output(
                    s3_uri=output_uri,
                    local_path="/opt/ml/processing/work/output/%s_assoc"
                    % CASE_CODE,
                    s3_upload_mode="Continuous",
                ),
            )
        ],
        job_name=job_name,
        wait=False,
        logs=False,
    )

    print("submitted: " + job_name)
    print("year:      {}".format(study_year))
    for prefix in pick_prefixes:
        print("picks:     s3://{}/{}".format(bucket, prefix))
    print("output:    " + output_uri)
    print("monitor:   python processing_job/monitor_assoc_job.py")
    print(
        "stop:      aws sagemaker stop-processing-job "
        "--processing-job-name " + job_name
    )


if __name__ == "__main__":
    main()
