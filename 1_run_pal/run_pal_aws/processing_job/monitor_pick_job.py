#!/usr/bin/env python3
"""Show SageMaker status and S3 picking progress for each PAL yearly job."""

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from statistics import median

import boto3
from botocore.config import Config as BotoConfig
from sagemaker.core.helper.session_helper import Session


# ============================================================================
# USER SETTINGS: edit paths, case identity, AWS resources, and runtime here
# ============================================================================
# AWS and PAL job settings
CASE_CODE = "eg"  # Packaged example; must match the submission CASE_CODE.
region = "us-west-2"
bucket = None  # None uses the active SageMaker default bucket.
results_prefix = "sagemaker/scsn-pal/results"
status_download_workers = 16
failed_reason_limit = 5
failed_example_limit = 3
runtime_limit_tolerance_seconds = 15 * 60
jobs = (
    {
        "label": "2020",
        "job_code": "%s-pick-2020" % CASE_CODE,
        "start": date(2020, 1, 1),
        "end": date(2021, 1, 1),
    },
    {
        "label": "2021",
        "job_code": "%s-pick-2021" % CASE_CODE,
        "start": date(2021, 1, 1),
        "end": date(2022, 1, 1),
    },
)


# ============================================================================
# CONNECTION CODE: normally no edits are needed below this line
# ============================================================================

def main():
    global bucket
    if bucket is None:
        session = Session(
            boto_session=boto3.Session(region_name=region)
        )
        bucket = session.default_bucket()
    sagemaker = boto3.client("sagemaker", region_name=region)
    s3 = boto3.client(
        "s3",
        region_name=region,
        config=BotoConfig(
            max_pool_connections=status_download_workers,
            retries={"max_attempts": 10, "mode": "adaptive"},
        ),
    )
    now = datetime.now(timezone.utc)

    def normalize_error(error):
        merge_match = re.search(
            r"same ids \(([^)]+)\).*differing sampling rates", error
        )
        if merge_match:
            return (
                "Mixed sampling rates within miniSEED segments ({})".format(
                    merge_match.group(1)
                )
            )
        if "Sampling rate differs:" in error:
            return "Differing sampling rates among selected components"
        if "Selected corner frequency is above Nyquist" in error:
            return "Configured upper filter corner is above Nyquist"
        return error
    print("PAL Processing Job Progress")
    print("Checked: {}\n".format(now.strftime("%Y-%m-%d %H:%M:%S UTC")))

    for job in jobs:
        job_code = job["job_code"]
        expected_days = (job["end"] - job["start"]).days
        status_prefix = (
            results_prefix + "/" + job_code + "/output/" + CASE_CODE + "/pick_status/"
        )

        response = sagemaker.list_processing_jobs(
            NameContains=job_code,
            SortBy="CreationTime",
            SortOrder="Descending",
            MaxResults=20,
        )
        attempts = [
            row for row in response.get("ProcessingJobSummaries", [])
            if row["ProcessingJobName"].startswith(job_code + "-")
        ]
        latest = attempts[0] if attempts else None

        done_objects = {}
        failed_objects = {}
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=status_prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                name = key.rsplit("/", 1)[-1]
                if name.endswith(".done.json"):
                    done_objects[name.removesuffix(".done.json")] = key
                elif name.endswith(".failed.json"):
                    failed_objects[name.removesuffix(".failed.json")] = key

        # A successful retry can leave an older failed object in S3. Done wins.
        failed_dates = set(failed_objects) - set(done_objects)
        done_dates = set(done_objects)
        status_objects = {
            failed_date: ("failed", failed_objects[failed_date])
            for failed_date in failed_dates
        }
        status_objects.update({
            done_date: ("done", done_objects[done_date])
            for done_date in done_dates
        })

        def load_status(status_item):
            observed_date, (status_kind, key) = status_item
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            return observed_date, status_kind, json.loads(body)

        reports = []
        unreadable = []
        if status_objects:
            with ThreadPoolExecutor(max_workers=status_download_workers) as pool:
                futures = {
                    pool.submit(load_status, item): item[0]
                    for item in status_objects.items()
                }
                for future in as_completed(futures):
                    try:
                        reports.append(future.result())
                    except Exception as exc:
                        unreadable.append((futures[future], repr(exc)))

        processed_dates = len(done_dates) + len(failed_dates)
        remaining_dates = max(0, expected_days - processed_dates)
        progress = 100.0 * processed_dates / expected_days

        total_attempted = 0
        total_failed = 0
        partial_attempted = 0
        partial_failed = 0
        partial_day_rates = []
        failed_stations = set()
        total_triggers = 0
        total_accepted_picks = 0
        trigger_report_dates = 0
        reasons = defaultdict(
            lambda: {"stations": 0, "dates": set(), "examples": []}
        )

        for observed_date, status_kind, report in reports:
            if "num_stalta_triggers" in report:
                total_triggers += int(report["num_stalta_triggers"])
                total_accepted_picks += int(
                    report.get("num_accepted_picks", 0)
                )
                trigger_report_dates += 1
            station_errors = report.get("station_errors", [])
            successful = int(report.get("stations_processed", 0))
            failed = len(station_errors)
            attempted = successful + failed
            if attempted == 0:
                attempted = int(
                    report.get("stations_with_usable_s3_components", 0)
                )

            total_attempted += attempted
            total_failed += failed

            if status_kind == "failed" or failed:
                partial_attempted += attempted
                partial_failed += failed
                if attempted:
                    partial_day_rates.append(100.0 * failed / attempted)

            for station_error in station_errors:
                net_sta = station_error.get("net_sta", "unknown")
                failed_stations.add(net_sta)
                error = normalize_error(" ".join(
                    str(station_error.get("error", "Unknown station error")).split()
                ))
                row = reasons[error]
                row["stations"] += 1
                row["dates"].add(observed_date)
                if len(row["examples"]) < failed_example_limit:
                    row["examples"].append(
                        "{} {}".format(observed_date, net_sta)
                    )

        print("{} ({})".format(job["label"], job_code))
        if latest is None:
            print("  latest job:  not submitted")
        else:
            name = latest["ProcessingJobName"]
            details = sagemaker.describe_processing_job(ProcessingJobName=name)
            status = details["ProcessingJobStatus"]
            started = details.get("ProcessingStartTime")
            ended = details.get("ProcessingEndTime")
            elapsed_end = ended or now
            elapsed = elapsed_end - started if started else None
            max_runtime = details.get("StoppingCondition", {}).get(
                "MaxRuntimeInSeconds"
            )

            print("  latest job:  {}".format(name))
            print("  status:      {}".format(status))
            if elapsed is not None:
                total_seconds = max(0, int(elapsed.total_seconds()))
                hours, remainder = divmod(total_seconds, 3600)
                minutes = remainder // 60
                if max_runtime:
                    max_hours, max_remainder = divmod(max_runtime, 3600)
                    max_minutes = max_remainder // 60
                    print(
                        "  runtime:     {}h {:02d}m / {}h {:02d}m maximum".format(
                            hours, minutes, max_hours, max_minutes
                        )
                    )
                else:
                    print("  runtime:     {}h {:02d}m".format(hours, minutes))
            if details.get("FailureReason"):
                print("  failure:     {}".format(details["FailureReason"]))
            if details.get("ExitMessage"):
                print("  exit:        {}".format(details["ExitMessage"]))
            if status == "Stopped" and elapsed is not None and max_runtime:
                difference = abs(elapsed.total_seconds() - max_runtime)
                if difference <= runtime_limit_tolerance_seconds:
                    print(
                        "  stop cause:  maximum runtime reached "
                        "(inferred from timing)"
                    )
                elif elapsed.total_seconds() < max_runtime:
                    print(
                        "  stop cause:  stopped before runtime limit; "
                        "check CloudTrail for StopProcessingJob"
                    )

        print(
            "  processed:   {} / {} dates ({:.1f}%)".format(
                processed_dates, expected_days, progress
            )
        )
        print("  clean dates: {}".format(len(done_dates)))
        print("  completed with station errors: {}".format(len(failed_dates)))
        print("  remaining:   {}".format(remaining_dates))
        if trigger_report_dates:
            acceptance = (
                100.0 * total_accepted_picks / total_triggers
                if total_triggers else 0.0
            )
            print(
                "  STA/LTA:     {:,} triggers -> {:,} QC-accepted picks "
                "({:.2f}%) across {} date(s)".format(
                    total_triggers, total_accepted_picks, acceptance,
                    trigger_report_dates,
                )
            )
        if status_objects:
            all_dates = set(status_objects)
            print("  date span:   {} to {}".format(min(all_dates), max(all_dates)))
        if unreadable:
            print("  unreadable:  {} status report(s)".format(len(unreadable)))

        if failed_dates:
            preview = ", ".join(sorted(failed_dates)[:10])
            if len(failed_dates) > 10:
                preview += ", ..."
            print("  dates with station errors: {}".format(preview))

            partial_rate = (
                100.0 * partial_failed / partial_attempted
                if partial_attempted else 0.0
            )
            overall_rate = (
                100.0 * total_failed / total_attempted
                if total_attempted else 0.0
            )
            print(
                "  explicitly failed stations on those dates: {} / {} ({:.3f}%)".format(
                    partial_failed, partial_attempted, partial_rate
                )
            )
            if partial_day_rates:
                print(
                    "  daily station-failure rate: min {:.3f}%, "
                    "median {:.3f}%, max {:.3f}%".format(
                        min(partial_day_rates),
                        median(partial_day_rates),
                        max(partial_day_rates),
                    )
                )
            print(
                "  failed station-date pairs: {} / {} ({:.3f}%)".format(
                    total_failed, total_attempted, overall_rate
                )
            )
            print("  unique failed stations: {}".format(len(failed_stations)))

            ranked_reasons = sorted(
                reasons.items(),
                key=lambda item: (
                    -len(item[1]["dates"]),
                    -item[1]["stations"],
                    item[0],
                ),
            )
            print("  failure categories:")
            for index, (error, summary) in enumerate(
                ranked_reasons[:failed_reason_limit], start=1
            ):
                print(
                    "    {}. {} station error(s) across {} date(s)".format(
                        index, summary["stations"], len(summary["dates"])
                    )
                )
                print("       {}".format(error))
                if summary["examples"]:
                    print(
                        "       examples: {}".format(
                            ", ".join(summary["examples"])
                        )
                    )
            hidden = len(ranked_reasons) - failed_reason_limit
            if hidden > 0:
                print("    ... {} less frequent reason(s)".format(hidden))

        print(
            "  output:      s3://{}/{}/{}/output/{}/\n".format(
                bucket, results_prefix, job_code, CASE_CODE
            )
        )


if __name__ == "__main__":
    main()
