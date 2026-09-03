"""Download one EarthScope FedCatalog Fullfed file per example network.

Edit ``NETWORKS`` below, then run:

    python 0.1_download_station_metadata_eg.py

For example, ``NETWORKS = ["CI"]`` creates
``input/station_ci_eg.fullfed`` for the station formatter.
"""
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# -----------------------------------------------------------------------------
# User parameter: enter one or more FDSN network codes.
CASE_CODE = "eg"
NETWORKS = ["CI"]

# These defaults retrieve all channel epochs since 1900. Normally only
# NETWORKS needs to be changed.
START_TIME = "2019-07-01"
END_TIME = "2019-08-01"
OUTPUT_DIRECTORY = "input"
OUTPUT_TEMPLATE = "station_{network}_%s.fullfed" % CASE_CODE
INCLUDE_OVERLAPS = True
REQUEST_TIMEOUT_SECONDS = 300
# -----------------------------------------------------------------------------


FEDCATALOG_URL = "https://service.earthscope.org/irisws/fedcatalog/1/query"
NETWORK_PATTERN = re.compile(r"^[A-Za-z0-9]{1,8}$")


def normalize_network(network):
    network = str(network).strip()
    if not NETWORK_PATTERN.fullmatch(network):
        raise ValueError(
            "invalid network code %r; use a 1-8 character alphanumeric code"
            % network)
    return network.upper()


def build_query_url(network):
    """Build a channel-level FedCatalog URL for one network."""
    params = {
        "network": normalize_network(network),
        "starttime": START_TIME,
        "format": "text",
        "level": "channel",
        "includeoverlaps": str(INCLUDE_OVERLAPS).lower(),
        "nodata": 404,
    }
    if END_TIME.strip():
        params["endtime"] = END_TIME.strip()
    return FEDCATALOG_URL + "?" + urlencode(params)


def download_text(url):
    request = Request(url, headers={"User-Agent": "AI-PAL-preprocess/1.0"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("FedCatalog found no metadata") from exc
        raise RuntimeError("FedCatalog returned HTTP %s" % exc.code) from exc
    except URLError as exc:
        raise RuntimeError("cannot reach EarthScope FedCatalog: %s" % exc.reason) from exc


def count_channel_epochs(text):
    """Count non-comment Fullfed channel rows."""
    return sum(
        1 for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        and line.count("|") >= 16)


def output_path(output_directory, network):
    filename = OUTPUT_TEMPLATE.format(network=normalize_network(network).lower())
    return output_directory / filename


def save_atomically(path, text):
    """Avoid leaving an incomplete final file after an interrupted write."""
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main():
    if not NETWORKS:
        raise ValueError("NETWORKS must contain at least one network code")

    output_directory = Path(__file__).resolve().parent / OUTPUT_DIRECTORY
    output_directory.mkdir(parents=True, exist_ok=True)
    failures = []

    # dict.fromkeys removes duplicates without changing the requested order.
    networks = list(dict.fromkeys(normalize_network(net) for net in NETWORKS))
    for network in networks:
        path = output_path(output_directory, network)
        url = build_query_url(network)
        print("[%s] querying %s" % (network, url))
        try:
            text = download_text(url)
            epoch_count = count_channel_epochs(text)
            if epoch_count == 0:
                raise RuntimeError("response contains no Fullfed channel rows")
            save_atomically(path, text)
            print("[%s] saved %s channel epochs to %s" %
                  (network, epoch_count, path))
        except (OSError, RuntimeError) as exc:
            failures.append((network, str(exc)))
            print("[%s] FAILED: %s" % (network, exc))

    if failures:
        print("\nCompleted with %s failed network(s):" % len(failures))
        for network, message in failures:
            print("  %s: %s" % (network, message))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
