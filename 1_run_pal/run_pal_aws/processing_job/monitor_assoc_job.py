#!/usr/bin/env python3
"""Show association jobs and aggregate daily screening metrics by month."""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import boto3
from botocore.config import Config as BotoConfig
from sagemaker.core.helper.session_helper import Session


# ============================================================================
# USER SETTINGS: edit paths, case identity, AWS resources, and runtime here
# ============================================================================
CASE_CODE = "eg"  # Packaged example; must match the submission CASE_CODE.
region = "us-west-2"
bucket = None  # None uses the active SageMaker default bucket.
results_prefix = "sagemaker/scsn-pal/results"
num_subnets = 6
max_failure_reports = 200
max_report_workers = 24
jobs = (
    {"label": "2020", "job_code": "%s-assoc-2020" % CASE_CODE,
     "start": date(2020, 1, 1), "end": date(2021, 1, 1)},
    {"label": "2021", "job_code": "%s-assoc-2021" % CASE_CODE,
     "start": date(2021, 1, 1), "end": date(2022, 1, 1)},
)


# ============================================================================
# CONNECTION CODE: normally no edits are needed below this line
# ============================================================================

def expected_dates(start, end):
    return [
        (start + timedelta(days=index)).isoformat()
        for index in range((end - start).days)
    ]


def expected_months(start, end):
    months = []
    current = start
    while current < end:
        months.append(current.strftime("%Y-%m"))
        current = (
            date(current.year + 1, 1, 1) if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    return months


def latest_job(sagemaker, job_code):
    response = sagemaker.list_processing_jobs(
        NameContains=job_code, SortBy="CreationTime",
        SortOrder="Descending", MaxResults=20,
    )
    rows = [
        row for row in response.get("ProcessingJobSummaries", [])
        if row["ProcessingJobName"].startswith(job_code + "-")
    ]
    return rows[0] if rows else None


def status_objects(s3, prefix):
    done, failed = {}, {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith(".done.json"):
                done[key.removesuffix(".done.json")] = key
            elif key.endswith(".failed.json"):
                failed[key.removesuffix(".failed.json")] = key
    active_failed = {
        stem: key for stem, key in failed.items() if stem not in done
    }
    return done, active_failed



def load_json_objects(s3, objects):
    if not objects:
        return {}

    def load(item):
        stem, key = item
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return stem, json.loads(body)

    worker_count = min(max_report_workers, len(objects))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return dict(executor.map(load, sorted(objects.items())))


def print_raw_subnet_health(raw_reports, expected_days):
    if not raw_reports:
        return
    rows = defaultdict(lambda: {
        "days": 0,
        "picks": 0,
        "associated": 0,
        "events": 0,
        "station_days": 0,
    })
    for report in raw_reports.values():
        subnet = report.get("subnet", "unknown")
        row = rows[subnet]
        row["days"] += 1
        row["picks"] += int(report.get("num_buffered_input_picks", 0))
        row["associated"] += int(report.get("num_associated_picks", 0))
        row["events"] += int(report.get("num_events", 0))
        row["station_days"] += int(report.get("num_stations", 0))

    print("  raw subnet health (before duplicate merging):")
    print(
        "    subnet   days  progress   buffered picks   associated  "
        "assoc %    events  evt/day  avg sta"
    )
    totals = {
        "days": 0, "picks": 0, "associated": 0,
        "events": 0, "station_days": 0,
    }
    for subnet in sorted(rows):
        row = rows[subnet]
        progress = 100.0 * row["days"] / expected_days if expected_days else 0.0
        ratio = (
            100.0 * row["associated"] / row["picks"]
            if row["picks"] else 0.0
        )
        events_per_day = row["events"] / row["days"] if row["days"] else 0.0
        average_stations = (
            row["station_days"] / row["days"] if row["days"] else 0.0
        )
        print(
            "    {:6s}  {:4d}/{:<4d}  {:6.1f}%  {:14,d}  {:11,d}  "
            "{:7.2f}  {:8,d}  {:7.2f}  {:7.1f}".format(
                subnet, row["days"], expected_days, progress,
                row["picks"], row["associated"], ratio, row["events"],
                events_per_day, average_stations,
            )
        )
        for key in totals:
            totals[key] += row[key]
    ratio = (
        100.0 * totals["associated"] / totals["picks"]
        if totals["picks"] else 0.0
    )
    events_per_day = (
        totals["events"] / totals["days"] if totals["days"] else 0.0
    )
    average_stations = (
        totals["station_days"] / totals["days"] if totals["days"] else 0.0
    )
    print(
        "    TOTAL   {:4d}/{:<4d}           {:14,d}  {:11,d}  "
        "{:7.2f}  {:8,d}  {:7.2f}  {:7.1f}".format(
            totals["days"], expected_days * num_subnets, totals["picks"],
            totals["associated"], ratio, totals["events"],
            events_per_day, average_stations,
        )
    )


def print_failure_reasons(s3, failed_objects, label):
    if not failed_objects:
        return
    groups = defaultdict(lambda: {"count": 0, "examples": []})
    items = dict(sorted(failed_objects.items())[:max_failure_reports])
    failure_reports = load_json_objects(s3, items)
    for report in failure_reports.values():
        error = report.get("error", "unknown error")
        group = groups[error]
        group["count"] += 1
        example = "{} {}".format(
            report.get("subnet", ""), report.get("date", "")
        ).strip()
        if example and len(group["examples"]) < 3:
            group["examples"].append(example)

    print("  {} failure reasons{}:".format(
        label,
        " (first {} reports)".format(len(items))
        if len(failed_objects) > len(items) else "",
    ))
    ranked = sorted(
        groups.items(), key=lambda item: (-item[1]["count"], item[0])
    )
    for index, (error, group) in enumerate(ranked[:10], start=1):
        print("    {}. {} failure(s)".format(index, group["count"]))
        print("       {}".format(error))
        if group["examples"]:
            print("       examples: {}".format(", ".join(group["examples"])))
    if len(ranked) > 10:
        print("       ... {} less frequent reason(s)".format(len(ranked) - 10))

def main():
    global bucket
    if bucket is None:
        session = Session(
            boto_session=boto3.Session(region_name=region)
        )
        bucket = session.default_bucket()
    config = BotoConfig(
        max_pool_connections=32,
        retries={"max_attempts": 10, "mode": "adaptive"},
    )
    sagemaker = boto3.client("sagemaker", region_name=region, config=config)
    s3 = boto3.client("s3", region_name=region, config=config)
    now = datetime.now(timezone.utc)
    print("PAL Association Job Progress")
    print("Checked: {}\n".format(now.strftime("%Y-%m-%d %H:%M:%S UTC")))

    for job in jobs:
        job_code = job["job_code"]
        output_prefix = results_prefix + "/" + job_code + "/output/assoc"
        status_prefix = output_prefix + "/assoc_status/"
        expected = expected_dates(job["start"], job["end"])
        done_keys, failed_keys = {}, {}
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=status_prefix):
            for item in page.get("Contents", []):
                name = item["Key"].rsplit("/", 1)[-1]
                if name.endswith(".done.json"):
                    done_keys[name.removesuffix(".done.json")] = item["Key"]
                elif name.endswith(".failed.json"):
                    failed_keys[name.removesuffix(".failed.json")] = item["Key"]
        failed_dates = set(failed_keys) - set(done_keys)
        reports = load_json_objects(s3, done_keys)

        print("{} ({})".format(job["label"], job_code))
        latest = latest_job(sagemaker, job_code)
        if latest is None:
            print("  latest job:  not submitted")
        else:
            name = latest["ProcessingJobName"]
            details = sagemaker.describe_processing_job(ProcessingJobName=name)
            print("  latest job:  {}".format(name))
            print("  status:      {}".format(details["ProcessingJobStatus"]))
            started = details.get("ProcessingStartTime")
            if started:
                ended = details.get("ProcessingEndTime") or now
                seconds = max(0, int((ended - started).total_seconds()))
                hours, remainder = divmod(seconds, 3600)
                print("  elapsed:     {}h {:02d}m".format(hours, remainder // 60))
            if details.get("FailureReason"):
                print("  failure:     {}".format(details["FailureReason"]))

        completed = [value for value in expected if value in reports]
        remaining = [
            value for value in expected
            if value not in reports and value not in failed_dates
        ]
        raw_done_objects, raw_failed_objects = status_objects(
            s3, output_prefix + "/raw_status/"
        )
        merge_done_objects, merge_failed_objects = status_objects(
            s3, output_prefix + "/merge_status/"
        )
        raw_done, raw_failed = len(raw_done_objects), len(raw_failed_objects)
        raw_reports = load_json_objects(s3, raw_done_objects)
        merge_done, merge_failed = (
            len(merge_done_objects), len(merge_failed_objects)
        )
        expected_halo_days = len(expected) + 2
        print("  raw subnet-days: {} / {} ({} failed)".format(
            raw_done, expected_halo_days * num_subnets, raw_failed
        ))
        print("  canonical merges: {} / {} ({} failed)".format(
            merge_done, expected_halo_days, merge_failed
        ))
        print("  completed:   {} / {} days ({:.1f}%)".format(
            len(completed), len(expected),
            100.0 * len(completed) / len(expected) if expected else 0.0,
        ))
        print("  failed:      {}".format(len(failed_dates)))
        print("  remaining:   {}".format(len(remaining)))
        print_raw_subnet_health(raw_reports, expected_halo_days)
        print_failure_reasons(s3, raw_failed_objects, "raw association")
        print_failure_reasons(s3, merge_failed_objects, "canonical merge")
        print_failure_reasons(
            s3,
            {stem: failed_keys[stem] for stem in failed_dates},
            "daily finalization",
        )

        monthly = defaultdict(lambda: {
            "days": 0, "picks": 0, "associated": 0, "events": 0,
            "subnet_events": 0, "duplicates": 0,
        })
        for observed_date, report in reports.items():
            row = monthly[observed_date[:7]]
            row["days"] += 1
            row["picks"] += int(report.get("total_picks", 0))
            row["associated"] += int(report.get("associated_picks", 0))
            row["events"] += int(report.get("num_events", 0))
            row["subnet_events"] += int(report.get("num_input_subnet_events", 0))
            row["duplicates"] += int(report.get("num_duplicate_events_removed", 0))

        print("  monthly screening:")
        print("    month    days  STA/LTA trig  associated  assoc %   events  subnet events  duplicates")
        totals = {key: 0 for key in (
            "days", "picks", "associated", "events", "subnet_events", "duplicates"
        )}
        for month in expected_months(job["start"], job["end"]):
            row = monthly[month]
            ratio = 100.0 * row["associated"] / row["picks"] if row["picks"] else 0.0
            print(
                "    {}  {:4d}  {:11,d}  {:10,d}  {:7.2f}  {:7,d}  {:13,d}  {:10,d}".format(
                    month, row["days"], row["picks"], row["associated"], ratio,
                    row["events"], row["subnet_events"], row["duplicates"],
                )
            )
            for key in totals:
                totals[key] += row[key]
        ratio = 100.0 * totals["associated"] / totals["picks"] if totals["picks"] else 0.0
        print(
            "    TOTAL    {:4d}  {:11,d}  {:10,d}  {:7.2f}  {:7,d}  {:13,d}  {:10,d}".format(
                totals["days"], totals["picks"], totals["associated"], ratio,
                totals["events"], totals["subnet_events"], totals["duplicates"],
            )
        )
        if failed_dates:
            preview = ", ".join(sorted(failed_dates)[:10])
            if len(failed_dates) > 10:
                preview += ", ..."
            print("  failed dates: {}".format(preview))
        print("  output:      s3://{}/{}/\n".format(bucket, output_prefix))


if __name__ == "__main__":
    main()





