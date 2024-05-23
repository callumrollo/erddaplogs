from copy import copy
from datetime import datetime
from apachelogs import LogParser
from pathlib import Path
import polars as pl
from collections import Counter
from user_agents import parse
import requests
import re
import gzip
import xml.etree.ElementTree as ET


def _load_apache_logs(apache_logs_dir,wildcard_fname):
    """
    Parses apache logs.

    Parameters
    ----------
    apache_logs_dir: str
        dir with apache log files
    wildcard_fname: str
        apache access logfile name string allowing for wildcard
    Returns
    -------
    polars.DataFrame
        parsed requests information
    """
    apache_logs = list(Path(apache_logs_dir).glob(wildcard_fname))
    if len(apache_logs) == 0:
        raise ValueError(
            f"Supplied directory {apache_logs_dir} contains no access.log files",
        )
    parser = LogParser('%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"')
    dt, ip, url, ua, code, bytes_sent, referer = [], [], [], [], [], [], []
    for fn in apache_logs:
        with open(fn) as fp:
            for entry in parser.parse_lines(fp):
                try:
                    this_url = entry.request_line.split(" ")[1]
                except IndexError:
                    this_url = ""
                dt.append(entry.request_time)
                ip.append(entry.remote_host)
                url.append(this_url)
                ua.append(entry.headers_in["User-Agent"])
                code.append(entry.final_status)
                bytes_sent.append(entry.bytes_sent)
                referer.append(entry.headers_in["Referer"])
    df = pl.DataFrame(
        {
            "ip": ip,
            "datetime": dt,
            "url": url,
            "user-agent": ua,
            "status-code": code,
            "bytes-sent": bytes_sent,
            "referer": referer,
        }
    ).with_columns(pl.col("datetime").dt.replace_time_zone(None))
    df = df.with_columns(pl.col("status-code").cast(pl.Int64))
    df = df.with_columns(pl.col("bytes-sent").cast(pl.Int64))
    return df


def _load_nginx_logs(nginx_logs_dir, wildcard_fname):
    """
    Parses nginx logs.

    Parameters
    ----------
    nginx_logs_dir: str
        dir with apache log files
    wildcard_fname: str
        nginx access logfile name string allowing for wildcard
    Returns
    -------
    polars.DataFrame
        parsed requests information
    """
    # nginx log parser from https://gist.github.com/hreeder/f1ffe1408d296ce0591d
    csvs = list(Path(nginx_logs_dir).glob(wildcard_fname))
    if len(csvs) == 0:
        raise ValueError(
            f"Supplied directory {nginx_logs_dir} contains no tomcat-access.log files",
        )
    lineformat = re.compile(
        r"""(?P<ipaddress>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}) - - \[(?P<dateandtime>\d{2}/[a-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} ([+\-])\d{4})] ((\"(GET|POST|HEAD|PUT|DELETE) )(?P<url>.+)(http/(1\.1|2\.0)")) (?P<statuscode>\d{3}) (?P<bytessent>\d+) (?P<refferer>-|"([^"]+)") (["](?P<useragent>[^"]+)["])""",
        re.IGNORECASE,
    )
    ip, datetimestring, url, bytessent, referer, useragent, status, method = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for f in csvs:
        if str(f).endswith(".gz"):
            logfile = gzip.open(f)
        else:
            logfile = open(f)
        for line in logfile.readlines():
            data = re.search(lineformat, line)
            if data:
                datadict = data.groupdict()
                ip.append(datadict["ipaddress"])
                datetimestring.append(datadict["dateandtime"])
                url.append(datadict["url"])
                bytessent.append(datadict["bytessent"])
                referer.append(datadict["refferer"])
                useragent.append(datadict["useragent"])
                status.append(datadict["statuscode"])
                method.append(data.group(6))
        logfile.close()

    df = pl.DataFrame(
        {
            "ip": ip,
            "datetime": datetimestring,
            "url": url,
            "user-agent": useragent,
            "status-code": status,
            "bytes-sent": bytessent,
            "referer": referer,
        }
    )
    df = df.with_columns(pl.col("status-code").cast(pl.Int64))
    df = df.with_columns(pl.col("bytes-sent").cast(pl.Int64))
    # convert timestamp to datetime
    df = df.with_columns(
        pl.col("datetime")
        .str.strptime(pl.Datetime, format="%d/%b/%Y:%H:%M:%S +0000")
        .dt.replace_time_zone(None)
    )
    df_nginx = df.sort(by="datetime")
    return df_nginx


def _get_ip_info(df, ip_info_csv, download_new=True, num_new_ips=60, verbose=False):
    """
    Add ip-derived information to the requests DataFrame.

    If it exists, read a .csv file with ip-derived info. If said file does
    not exist, get ip-derived information from requests ip addresses
    using http://ip-api.com. Add this info to the requests DataFrame and
    create a csv file with the ip-derived information.

    Parameters
    ----------
    df: polars.DataFrame
        parsed requests information
    ip_info_csv: str
        path to the csv file where ip information will be saved
    download_new: bool, default=True
        if True, fetches information for unknown ip addresses
    num_new_ips: int, default=60
        number of new ip addresses to fetch information for
    verbose: bool, default=False
        if True, info from each newly identified ip address will be displayed on the screen

    Returns
    -------
    polars.DataFrame
        ip-derived information
    """
    ip_counts = Counter(df["ip"]).most_common()
    if Path(ip_info_csv).exists():
        df_ip = pl.read_csv(ip_info_csv)
    else:
        df_ip = pl.DataFrame(
            {
                "status": "",
                "country": "",
                "countryCode": "",
                "region": "",
                "regionName": "",
                "city": "",
                "zip": "",
                "lat": 0.0,
                "lon": 0.0,
                "timezone": "",
                "isp": "",
                "org": "",
                "as": "",
                "query": "",
            }
        )
    if download_new:
        fetched_ips = 0
        for ip, count in ip_counts:
            if ip not in df_ip["query"]:
                if fetched_ips >= num_new_ips:
                    break
                resp_raw = requests.get(f"http://ip-api.com/json/{ip}")
                fetched_ips += 1
                if resp_raw.status_code == 429:
                    print("Exceeded API responses. Wait a minute and try again")
                    break
                resp = resp_raw.json()
                if verbose:
                    if "country" in resp.keys():
                        print(
                            f"New ip identified: {ip} in {resp['country']}. Sent {count} requests"
                        )
                    else:
                        print(f"New ip identified: {ip}. Sent {count} requests")
                try:
                    df_ip = pl.concat((df_ip, pl.DataFrame(resp)), how="diagonal")
                except (pl.exceptions.SchemaError, pl.exceptions.ShapeError):
                    print(f"Issue fetching data for this ip address {ip}, skipping")
    df_ip.write_csv(ip_info_csv)
    if verbose:
        print(f"We have info on {len(df_ip)} ip address")
    return df_ip


def _parse_columns(df):
    """
    Parses the requests and other columns to generate extra columns of data

    Get base_url, request_kwargs and file_type
    from the request url. Discard the versions
    of user-agents and separate ip addresses into
    groups and subnets.

    Parameters
    ----------
    df: polars.DataFrame
        DataFrame with requests information

    Returns
    -------
    polars.DataFrame
        requests DataFrame with additional information, suitable for plotting
    """
    df = df.with_columns(pl.col("country").fill_null("unknown"))
    df_parts = df["url"].to_pandas().str.replace(" ", "").str.split("?", expand=True)
    df = df.with_columns(base_url=df_parts[0].str.split(".", expand=True)[0].astype(str).values)
    url_parts = df["base_url"].to_pandas().str.split("/", expand=True)
    url_parts["protocol"] = None
    url_parts.loc[url_parts[2] == "tabledap", "protocol"] = "tabledap"
    url_parts.loc[url_parts[2] == "griddap", "protocol"] = "griddap"
    url_parts.loc[url_parts[2] == "files", "protocol"] = "files"
    url_parts.loc[url_parts[2] == "info", "protocol"] = "info"
    url_parts["dataset_id"] = url_parts[3]
    df = df.with_columns(erddap_request_type=url_parts["protocol"].astype(str).values)
    df = df.with_columns(dataset_id=url_parts["dataset_id"].astype(str).values)
    df = df.with_columns(
        dataset_id=pl.when(pl.col("erddap_request_type").is_null())
        .then(None)
        .otherwise(pl.col("dataset_id"))
    )
    df = df.with_columns(request_kwargs=df_parts[1].astype(str).values)
    df = df.with_columns(file_type=df_parts[0].str.split(".", expand=True)[1].astype(str).values)
    df = df.with_columns(
        user_agent_base=df["user-agent"]
        .to_pandas()
        .str.split(" ", expand=True)[0]
        .str.split("/", expand=True)[0]
        .values
    )
    ip_grid = df["ip"].to_pandas().str.split(".", expand=True)
    ip_group = ip_grid[0] + "." + ip_grid[1]
    ip_subnet = ip_grid[0] + "." + ip_grid[1] + "." + ip_grid[2]
    df = df.with_columns(ip_group=ip_group.values)
    df = df.with_columns(ip_subnet=ip_subnet.values)
    df = df.sort(by="datetime")

    return df


def _print_filter_stats(call_wrap):
    """
    Decorator to the filter methods.

    Modify filter_* methods so they print dataset information
    before and after filtering.
    """

    def magic(self):
        len_before = len(self.df)
        call_wrap(self)
        if self.verbose:
            print(
                f"Filter {self.filter_name} dropped {len_before - len(self.df)} lines. Length of dataset is now "
                f"{int(len(self.df) / self.original_total_requests * 100)} % of original"
            )

    return magic


class ErddapLogParser:
    def __init__(self):
        self.df = pl.DataFrame()
        self.ip = pl.DataFrame()
        self.df_xml = pl.DataFrame()
        self.verbose = False
        self.original_total_requests = 0
        self.filter_name = None

    def _update_original_total_requests(self):
        """Update the number of requests in the DataFrame."""
        self.original_total_requests = len(self.df)
        self.unfiltered_df = copy(self.df)
        if self.verbose:
            print(f"DataFrame now has {self.original_total_requests} lines")

    def subset_df(self, rows=1000):
        """Subset the requests DataFrame. Default rows=1000."""
        stride = int(self.df.shape[0] / rows)
        if self.verbose:
            print(
                f"starting from DataFrame with {self.df.shape[0]} lines. Subsetting by a factor of {stride}"
            )
        self.df = self.df.gather_every(stride)
        if self.verbose:
            print(
                "resetting number of original total requests to match subset DataFrame"
            )
        self._update_original_total_requests()

    def load_apache_logs(self, apache_logs_dir: str, wildcard_fname: str):
        """Parse apache logs."""
        df_apache = _load_apache_logs(apache_logs_dir, wildcard_fname="*access.log*")
        if self.verbose:
            print(f"loaded {len(df_apache)} log lines from {apache_logs_dir}")
        df_combi = pl.concat(
            [
                self.df,
                df_apache,
            ],
            how="vertical",
        )
        df_combi = df_combi.sort("datetime").unique()
        self.df = df_combi
        self._update_original_total_requests()

    def load_nginx_logs(self, nginx_logs_dir: str, wildcard_fname: str):
        """Parse nginx logs."""
        df_nginx = _load_nginx_logs(nginx_logs_dir, wildcard_fname="*access.log*")
        if self.verbose:
            print(f"loaded {len(df_nginx)} log lines from {nginx_logs_dir}")
        df_combi = pl.concat(
            [
                self.df,
                df_nginx,
            ],
            how="vertical",
        )
        df_combi = df_combi.sort("datetime").unique()
        self.df = df_combi
        self._update_original_total_requests()

    def get_ip_info(self, ip_info_csv="ip.csv", download_new=True, num_ips=60):
        """Get ip-derived information from requests ip addresses."""
        if "country" in self.df.columns:
            return
        df_ip = _get_ip_info(
            self.df,
            ip_info_csv,
            download_new=download_new,
            verbose=self.verbose,
            num_new_ips=num_ips,
        )
        self.ip = df_ip
        self.df = self.df.join(df_ip, left_on="ip", right_on="query", how='left').sort("datetime")

    @_print_filter_stats
    def filter_non_erddap(self):
        """Filter out non-genuine requests."""
        self.filter_name = "non erddap"
        self.df = self.df.filter(pl.col("url").str.contains("erddap"))

    @_print_filter_stats
    def filter_organisations(self, organisations=("Google", "Crawlers", "SEMrush")):
        """Filter out non-visitor requests from specific organizations."""
        if "org" not in self.df.columns:
            raise ValueError(
                "Organisation information not present in DataFrame. Try running get_ip_info first.",
            )
        self.df = self.df.with_columns(pl.col("org").fill_null("unknown"))
        self.df = self.df.with_columns(pl.col("isp").fill_null("unknown"))
        for block_org in organisations:
            self.df = self.df.filter(~pl.col("org").str.contains(f"(?i){block_org}"))
            self.df = self.df.filter(~pl.col("isp").str.contains(f"(?i){block_org}"))
        self.filter_name = "organisations"

    @_print_filter_stats
    def filter_user_agents(self):
        """Filter out requests from bots."""
        # Added by Samantha Ouertani at NOAA AOML Jan 2024
        self.df = self.df.filter(
            ~pl.col("user-agent").map_elements(
                lambda ua: parse(ua).is_bot, return_dtype=pl.Boolean
            )
        )
        self.filter_name = "user agents"

    @_print_filter_stats
    def filter_locales(self, locales=("zh-CN", "zh-TW", "ZH")):
        # Added by Samantha Ouertani at NOAA AOML Jan 2024
        """Filter out requests from specific regions (locales)."""
        for locale in locales:
            self.df = self.df.filter(~pl.col("url").str.contains(f"{locale}"))
        self.filter_name = "locales"

    @_print_filter_stats
    def filter_spam(
        self,
        spam_strings=(
            ".env",
            "env.",
            ".php",
            ".git",
            "robots.txt",
            "phpinfo",
            "/config",
            "aws",
            ".xml",
        ),
    ):
        """
        Filter out requests from non-visitors.

        Filter out requests from indexing webpages, services monitoring uptime,
        requests for files that aren't on the server, etc
        """
        page_counts = Counter(
            list(self.df.select("url").to_numpy()[:, 0])
        ).most_common()
        bad_pages = []
        for page, count in page_counts:
            for phrase in spam_strings:
                if phrase in page:
                    bad_pages.append(page)
        self.df = self.df.filter(~pl.col("url").is_in(bad_pages))
        self.filter_name = "spam"

    @_print_filter_stats
    def filter_files(self):
        """Filter out requests for browsing erddap's virtual file system."""
        # Added by Samantha Ouertani at NOAA AOML Jan 2024
        self.df = self.df.filter(~pl.col("url").str.contains("/files"))
        self.filter_name = "files"

    @_print_filter_stats
    def filter_common_strings(
        self, strings=("/version", "favicon.ico", ".js", ".css", "/erddap/images")
    ):
        """Filter out non-data requests - requests for version, images, etc"""
        for string in strings:
            self.df = self.df.filter(~pl.col("url").str.contains(string))
        self.filter_name = "common strings"

    def parse_datasets_xml(self, datasets_xml_path):
        tree = ET.parse(datasets_xml_path)
        root = tree.getroot()
        dataset_id = []
        dataset_type = []
        for child in root:
            if 'datasetID' in child.keys():
                dataset_id.append(child.get('datasetID'))
                dataset_type.append(child.get('type'))
        self.df_xml = pl.DataFrame({'dataset_id': dataset_id, 'dataset_type': dataset_type})

    def parse_columns(self):
        self.df = _parse_columns(self.df)
        if not self.df_xml.is_empty():
            self.df = self.df.join(self.df_xml, left_on="dataset_id", right_on="dataset_id", how='left').sort("datetime")

    def aggregate_location(self):
        """Generates a dataframe that contains query counts by status code and location."""
        self.location = self.df.group_by(["countryCode", "regionName", "city"]).len()

    def anonymize_user_agent(self):
        """Modifies the anonymized dataframe to have browser, device, and os names instead of full user agent."""
        self.anonymized = self.anonymized.with_columns(
            pl.col("user-agent")
            .map_elements(lambda ua: parse(ua).browser.family, return_dtype=pl.String)
            .alias("BrowserFamily")
        )
        self.anonymized = self.anonymized.with_columns(
            pl.col("user-agent")
            .map_elements(lambda ua: parse(ua).device.family, return_dtype=pl.String)
            .alias("DeviceFamily")
        )
        self.anonymized = self.anonymized.with_columns(
            pl.col("user-agent")
            .map_elements(lambda ua: parse(ua).os.family, return_dtype=pl.String)
            .alias("OS")
        )
        self.anonymized = self.anonymized.drop("user-agent")

    def anonymize_ip(self):
        """Replaces the ip address with a unique number identifier."""
        unique_df = pl.DataFrame(
            {"ip": self.anonymized.get_column("ip").unique()}
        ).with_row_index()
        self.anonymized = self.anonymized.with_columns(
            pl.col("ip").map_elements(
                lambda ip: unique_df.row(by_predicate=(pl.col("ip") == ip), named=True)[
                    "index"
                ],
                return_dtype=pl.Int32,
            )
        )

    def anonymize_query(self):
        """Remove email= and the address from queries."""
        self.anonymized = self.anonymized.with_columns(
            pl.col("url").map_elements(
                lambda url: re.sub("email=.*?&", "", url),
                return_dtype=pl.String,
            )
        )

    def anonymize_requests(self):
        """Creates tables that are safe for sharing, including a query by location table and an anonymized table."""
        self.aggregate_location()
        self.anonymized = self.df.select(
            pl.selectors.matches("^^ip$|^datetime$|^status-code$|^bytes-sent$|^erddap_request_type$|^dataset_type$|^dataset_id$|^file_type$|^url$|^user-agent$")
        )
        self.anonymize_user_agent()
        self.anonymize_ip()
        self.anonymize_query()

    def export_data(self):
        """Exports the anonymized data to csv files that can be shared."""
        self.anonymize_requests()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_")
        self.anonymized.write_csv(timestamp + "anonymized.csv")
        self.location.write_csv(timestamp + "location.csv")

    def undo_filter(self):
        """Reset to unfiltered DataFrame."""
        if self.verbose:
            print("Reset to unfiltered DataFrame")
        self.df = self.unfiltered_df
        self._update_original_total_requests()
