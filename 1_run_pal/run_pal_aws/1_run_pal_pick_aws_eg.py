#!/usr/bin/env python3
"""Submit one yearly rule-based PAL picking job for the SCEDC AWS archive."""

import json
import shutil
import tempfile
from datetime import datetime, timezone
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
station_file = "station_scedc_aws_selected_20200101_20260701_pal.csv"
time_range = "20200101-20210101"  # Exclusive end date.
study_year = 2020

num_workers = 16
overwrite = False
retry_failed_dates = False

scedc_bucket = "scedc-pds"
scedc_region = "us-west-2"
scedc_root_prefix = "continuous_waveforms"
scedc_access_mode = "signed"
location_priority = ()
acceleration_instrument_codes = ("N",)

# ============================================================================
# USER SETTINGS: AWS RESOURCES, OUTPUTS, AND RESUME BEHAVIOR
# ============================================================================
region = "us-west-2"
results_s3_prefix = "sagemaker/scsn-pal/results"
resume_existing_output = True

instance_type = "ml.c5.9xlarge"
instance_count = 1
threads_per_worker = 2
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
job_code = "%s-pick-%d" % (CASE_CODE, study_year)
pal_files = (
    "data_pipeline_aws.py",
    "pick_runner.py",
    "run_pick_aws.py",
    "picker_pal.py",
    "trigger_counts.py",
)


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

    station_path = workflow_dir / "input" / station_file
    local_config = workflow_dir / case_config_file
    entry = processing_dir / "processing_entry_pick.py"
    requirements = processing_dir / "requirements.txt"
    required = [station_path, local_config, entry, requirements]
    required.extend(pal_dir / name for name in pal_files)
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)

    session = Session(boto_session=boto3.Session(region_name=region))
    role = get_execution_role()
    bucket = session.default_bucket()
    s3 = boto3.client("s3", region_name=region)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job_name = job_code + "-" + timestamp
    stage_root = "sagemaker/scsn-pal/jobs/" + job_name
    source_prefix = stage_root + "/source"
    work_prefix = stage_root + "/work"
    output_prefix = (
        results_s3_prefix + "/" + job_code + "/output/" + CASE_CODE
    )
    output_uri = "s3://{}/{}/".format(bucket, output_prefix)

    runtime = {
        "case_code": CASE_CODE,
        "station_file": "input/" + station_file,
        "pick_dir": "output/%s/picks" % CASE_CODE,
        "log_dir": "output/%s/logs" % CASE_CODE,
        "time_range": time_range,
        "num_workers": num_workers,
        "overwrite": overwrite,
        "retry_failed_dates": retry_failed_dates,
        "bucket": scedc_bucket,
        "region": scedc_region,
        "root_prefix": scedc_root_prefix,
        "access_mode": scedc_access_mode,
        "location_priority": list(location_priority),
        "acceleration_instrument_codes": list(
            acceleration_instrument_codes
        ),
    }

    with tempfile.TemporaryDirectory(prefix="pal-pick-") as temp_dir:
        stage = Path(temp_dir)
        source_stage = stage / "source"
        work_stage = stage / "work"
        (work_stage / "input").mkdir(parents=True)
        source_stage.mkdir(parents=True)

        shutil.copy2(local_config, work_stage / case_config_file)
        shutil.copy2(station_path, work_stage / "input" / station_file)
        shutil.copy2(requirements, work_stage / "requirements.txt")
        (work_stage / "pick_runtime.json").write_text(
            json.dumps(runtime, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
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
                output_name="picks",
                s3_output=ProcessingS3Output(
                    s3_uri=output_uri,
                    local_path="/opt/ml/processing/work/output/%s"
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
    print("waveforms: s3://{}/{}/".format(
        scedc_bucket, scedc_root_prefix
    ))
    print("output:    " + output_uri)
    print("monitor:   python processing_job/monitor_pick_job.py")
    print(
        "stop:      aws sagemaker stop-processing-job "
        "--processing-job-name " + job_name
    )


if __name__ == "__main__":
    main()
