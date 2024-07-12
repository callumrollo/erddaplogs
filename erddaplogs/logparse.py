import datetime
import os
from copy import copy
from pathlib import Path
import polars as pl
from collections import Counter
from user_agents import parse
import requests
import re
import gzip
import xml.etree.ElementTree as ET
_date_format_dict = {'day': '%Y-%m-%d',
                     'month': '%Y-%m',
                     'year': '%Y', }

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
    # nginx log parser from  Harry Reeder @hreeder https://gist.github.com/hreeder/f1ffe1408d296ce0591d
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
                url.append(datadict["url"].strip())
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
            "user_agent": useragent,
            "status_code": status,
            "bytes_sent": bytessent,
            "referer": referer,
        }
    )

    df = df.filter(pl.col("status_code") != "NaN").with_columns(pl.col("status_code").cast(pl.Int64))
    df = df.filter(pl.col("bytes_sent") != "NaN").with_columns(pl.col("bytes_sent").cast(pl.Int64))
    # convert timestamp to datetime
    df = df.with_columns(
        pl.col("datetime")
        .str.strptime(pl.Datetime, format="%d/%b/%Y:%H:%M:%S %z")
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
                "message": "",
            }
        )
    if download_new:
        if verbose:
            unkown_ips = len(set(df['ip'].unique().to_list()).difference(set(df_ip['query'])))
            if unkown_ips == 0:
                print("No new ips to fetch!")
                return df_ip
            print(f"We have info on {len(df_ip)} addresses. Dataset contains {len(ip_counts)} address, of which "
                  f"{unkown_ips} are not yet known")
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
    of user_agents and separate ip addresses into
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
    df = df.with_columns(
        base_url=df_parts[0].str.split(".", expand=True)[0].astype(str).values
    )
    url_parts = df["base_url"].to_pandas().str.split("/", expand=True)
    url_parts["protocol"] = ""
    request_types = ['tabledap', 'subscriptions', 'info', 'files', 'legal', 'convert',
                     'griddap', 'categorize', 'index', 'dataProviderForm', 'metadata',
                     'information', 'status', 'search', 'slidesorter', 'rest', 'sos', 'wcs'
                     'wms', 'dataProviderForm', 'rss', 'outOfDateDatasets', 'sitemap',
                     'download', 'images', 'public', 'opensearch1.1', 'logout', 'setDatasetFlag']
    for protocol in request_types:
        url_parts.loc[url_parts[2] == protocol, "protocol"] = protocol
    url_parts.loc[url_parts[2].str[:5] == 'login', "protocol"] = 'login'
    url_parts.loc[url_parts[2].str[:7] == 'version', "protocol"] = 'version'
    url_parts.loc[url_parts[2].str[:16] == 'dataProviderForm', "protocol"] = 'dataProviderForm'
    url_parts["dataset_id"] = url_parts[3]
    df = df.with_columns(erddap_request_type=url_parts["protocol"].astype(str).values)
    df = df.with_columns(dataset_id=url_parts["dataset_id"].astype(str).values)
    df = df.with_columns(
        dataset_id=pl.when(pl.col("erddap_request_type").is_null())
        .then(None)
        .otherwise(pl.col("dataset_id"))
    )
    df = df.with_columns(request_kwargs=df_parts[1].astype(str).values)
    df = df.with_columns(
        file_type=df_parts[0].str.split(".", expand=True)[1].astype(str).values
    )
    df = df.with_columns(
        user_agent_base=df["user_agent"]
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


def _parse_language_data(df):
    """
    Parses the langauge data from requests urls. Adds two additional columns with the language codes as they
    appear in the original urls and the named languages. Then, it removes the language section from the original
    url so that it does not affect subsequent classification (e.g. of tabledap vs griddap vs files)
    Parameters
    ----------
    df: polars.DataFrame
        DataFrame with requests information

    Returns
    -------
    polars.DataFrame
        requests DataFrame with parsed language information
    """
    # langauge codes taken from the ERDDAP source code WEB-INF/classes/gov/noaa/pfel/erddap/util/TranslateMessages.java

    language_codes = ["en", "bn", "zh-CN", "zh-TW", "cs", "da", "nl", "fi",
                      "fr", "de", "el", "gu", "hi", "hu", "id", "ga", "it",
                      "ja", "ko", "mr", "no", "pl", "pt",
                      "pa", "ro", "ru", "es", "sw", "sv", "tl", "th",
                      "tr", "uk", "ur", "vi"]

    language_names = ["English", "Bengali", "Chinese-CN", "Chinese-TW", "Czech", "Danish", "Dutch", "Finnish",
                      "French", "German", "Greek", "Gujarati", "Hindi", "Hungarian", "Indonesian", "Irish", "Italian",
                      "Japanese", "Korean", "Marathi", "Norwegian", "Polish", "Portuguese",
                      "Punjabi", "Romanian", "Russian", "Spanish", "Swahili", "Swedish", "Tagalog", "Thai",
                      "Turkish", "Ukrainian", "Urdu", "Vietnamese"]
    langauge_names_by_code = {code: name for code, name in zip(language_codes, language_names)}

    df = df.with_columns(
        pl.col("url")
        .str.split_exact("/", 3, inclusive=True)
        .struct.rename_fields(["blank", "root", "first", "rest"])
        .alias("fields")
    ).unnest("fields")
    df = df.with_columns(
        pl.col("url")
        .str.split_exact("/", 3)
        .struct.rename_fields(["blank_noslash", "root_noslash", "first_noslash", "rest_noslash"])
        .alias("fields")
    ).unnest("fields")

    potential_langauge_col = df['first_noslash'].to_numpy()
    corrected_language_col = df['first'].to_numpy()
    languages_present = set(df['first_noslash'].unique().to_numpy()).intersection(language_codes)
    language_col = potential_langauge_col.copy()
    language_col[:] = "English"
    language_code_col = potential_langauge_col.copy()
    language_code_col[:] = "en"

    for language_code in languages_present:
        corrected_language_col[potential_langauge_col == language_code] = ""
        language_code_col[potential_langauge_col == language_code] = language_code
        language_col[potential_langauge_col == language_code] = langauge_names_by_code[language_code]

    df = df.with_columns(language_code=language_code_col)
    df = df.with_columns(language=language_col)
    df = df.with_columns(first=corrected_language_col)
    df = df.with_columns(pl.concat_str(["blank", "root", "first", "rest"]).alias('reconstruct'))

    if "rest" in df.columns:
        df = df.drop(["blank", "root", "first", "rest"])
    if "rest_noslash" in df.columns:
        df = df.drop(["blank_noslash", "root_noslash", "first_noslash", "rest_noslash"])
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
        self.anonymized = pl.DataFrame()
        self.location = pl.DataFrame()
        self.df_xml = pl.DataFrame()
        self.verbose = False
        self.original_total_requests = 0
        self.filter_name = None
        self.temporal_resolution = 'month'

    def _update_original_total_requests(self):
        """Update the number of requests in the DataFrame."""
        self.original_total_requests = len(self.df)
        self.unfiltered_df = copy(self.df)
        if self.verbose:
            print(f"DataFrame now has {self.original_total_requests} lines")

    def subset_df(self, rows=1000):
        if self.df.shape[0] < rows:
            print(
                f"DataFrame length {self.df.shape[0]} lines is less than requested {rows} rows. Returning"
            )
            return
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

    def load_apache_logs(self, apache_logs_dir: str, wildcard_fname="*access.log*"):
        """Parse apache logs."""
        df_apache = _load_nginx_logs(apache_logs_dir, wildcard_fname)
        if self.verbose:
            print(f"loaded {len(df_apache)} log lines from {apache_logs_dir}")
        df_combi = pl.concat(
            [
                self.df,
                df_apache,
            ],
            how="vertical",
        )
        df_combi = df_combi.unique().sort("datetime")
        self.df = df_combi
        self._update_original_total_requests()

    def load_nginx_logs(self, nginx_logs_dir: str, wildcard_fname="*access.log*"):
        """Parse nginx logs."""
        df_nginx = _load_nginx_logs(nginx_logs_dir, wildcard_fname)
        if self.verbose:
            print(f"loaded {len(df_nginx)} log lines from {nginx_logs_dir}")
        df_combi = pl.concat(
            [
                self.df,
                df_nginx,
            ],
            how="vertical",
        )
        df_combi = df_combi.unique().sort("datetime")
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
        self.df = self.df.join(df_ip, left_on="ip", right_on="query", how="left").sort(
            "datetime"
        )

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
            ~pl.col("user_agent").map_elements(
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
            if "datasetID" in child.keys():
                dataset_id.append(child.get("datasetID"))
                dataset_type.append(child.get("type"))
        dataset_id.append("allDatasets")
        dataset_type.append("allDatasets")
        self.df_xml = pl.DataFrame(
            {"dataset_id": dataset_id, "dataset_type": dataset_type}
        )

    def parse_columns(self):
        self.df = _parse_language_data(self.df)
        self.df = _parse_columns(self.df)
        if not self.df_xml.is_empty():
            self.df = self.df.join(
                self.df_xml, left_on="dataset_id", right_on="dataset_id", how="left"
            ).sort("datetime")
            self.df = self.df.with_columns(
                dataset_id=pl.when(pl.col("dataset_type").is_null())
                .then(None)
                .otherwise(pl.col("dataset_id"))
            )

        self.df = self.df.with_columns(
            pl.when(pl.col(pl.String).str.len_chars() == 0)
            .then(None)
            .otherwise(pl.col(pl.String))
            .name.keep()
        )

    def aggregate_location(self):
        """Generates a dataframe that contains query counts by status code and location."""
        df = self.df
        self.location = (
            df.group_by(["countryCode", "regionName", "city", self.temporal_resolution])
            .len()
            .fill_null("unknown")
            .rename({"len": "total_requests"})
        ).cast({"total_requests": pl.Int64})[self.temporal_resolution, "countryCode", "regionName", "city", "total_requests"]

    def anonymize_user_agent(self):
        """Modifies the anonymized dataframe to have browser, device, and os names instead of full user agent."""
        self.anonymized = self.anonymized.with_columns(
            pl.col("user_agent")
            .map_elements(lambda ua: parse(ua).browser.family, return_dtype=pl.String)
            .alias("BrowserFamily")
        )
        self.anonymized = self.anonymized.with_columns(
            pl.col("user_agent")
            .map_elements(lambda ua: parse(ua).device.family, return_dtype=pl.String)
            .alias("DeviceFamily")
        )
        self.anonymized = self.anonymized.with_columns(
            pl.col("user_agent")
            .map_elements(lambda ua: parse(ua).os.family, return_dtype=pl.String)
            .alias("OS")
        )
        self.anonymized = self.anonymized.drop("user_agent")

    def anonymize_ip(self):
        """Replaces the ip address with a unique number identifier."""
        df = self.anonymized
        df = df.with_columns((pl.col(self.temporal_resolution) + pl.col("ip")).alias('temporal_ip'))
        unique_df = (
            pl.DataFrame({"temporal_ip": df.get_column("temporal_ip").unique()})
            .with_row_index()
            .with_columns(pl.col("index").cast(str))
        )
        date_length = len(df[self.temporal_resolution][0])
        unique_df = unique_df.with_columns(
            (pl.col('temporal_ip').str.slice(0, date_length) + '_' + pl.col("index")).alias("temporal_ip_id"),
            (pl.col('temporal_ip').str.slice(date_length)).alias("ip")

        )
        df = df.with_columns(
            pl.col("temporal_ip").map_elements(
                lambda temporal_ip: unique_df.row(by_predicate=(pl.col("temporal_ip") == temporal_ip), named=True)[
                    "temporal_ip_id"
                ],
                return_dtype=pl.String,
            )
        )
        df = df.drop('ip').rename({"temporal_ip": "ip_id"})
        self.anonymized = df

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
            pl.selectors.matches(
                f"^^ip$|^datetime$|^status_code$|^bytes_sent$|^erddap_request_type$|^dataset_type$|^dataset_id$|^file_type$|^url$|^user_agent$|^{self.temporal_resolution}$"
            )
        )
        self.anonymize_user_agent()
        self.anonymize_ip()
        self.anonymize_query()

    def export_data(self, output_dir=Path(os.getcwd()), export_all=False):
        """Exports the anonymized data to csv files that can be shared."""
        if self.temporal_resolution not in _date_format_dict.keys(): 
            print(f"self.temporal resolution must be one of {_date_format_dict.keys()}. Can not export data")
            return
        self.df = self.df.with_columns(self.df["datetime"].dt.strftime(_date_format_dict[self.temporal_resolution])
                                       .alias(self.temporal_resolution))
        output_dir = Path(output_dir)
        time_unit = self.temporal_resolution
        if not output_dir.exists():
            output_dir.mkdir(parents=True)
        previous_anon_files = list(output_dir.glob("*anonymized_requests.csv"))
        if len(previous_anon_files) != 0 and not export_all:
            previous_anon_files.sort()
            most_recent_file = previous_anon_files[-1]
            df_last = pl.read_csv(most_recent_file)
            if not df_last.is_empty():
                last_request = df_last[self.temporal_resolution].max()
                self.df = self.df.filter(pl.col(self.temporal_resolution) >= last_request)

        if not self.df.is_empty():
            self.anonymize_requests()
            self.anonymized = self.anonymized.sort('datetime')
            if not self.anonymized.is_empty():
                dates = self.anonymized[time_unit].unique().sort()
                for date in dates:
                    df_sub = self.anonymized.filter(pl.col(time_unit) == date).sort('datetime')
                    fn = output_dir / f"{str(date)}_anonymized_requests.csv"
                    df_sub.write_csv(fn)
                    if self.verbose:
                        print(f"write file {fn}")
        existing_loc_files = list(output_dir.glob("*aggregated_locations.csv"))
        if len(existing_loc_files) != 0:
            old_locs = pl.read_csv(f"{str(output_dir)}/*aggregated_locations.csv")
            old_locs = old_locs.filter(pl.col(self.temporal_resolution) < self.df[self.temporal_resolution].min())
            if not old_locs.is_empty():
                old_locs = old_locs.with_columns(
                    old_locs.select(
                        pl.concat_str([pl.col(time_unit), pl.col("regionName"), pl.col("city")]).alias(
                            "month_region_city"
                        )
                    )
                )
                if not self.location.is_empty():
                    new_locs = self.location.with_columns(
                        self.location.select(
                            pl.concat_str([pl.col(time_unit), pl.col("regionName"), pl.col("city")]).alias(
                                "month_region_city"
                            )
                        )
                    )
                    df_vertical_concat = pl.concat(
                        [
                            old_locs,
                            new_locs,
                        ],
                        how="vertical",
                    )
                    totals = (
                        df_vertical_concat.group_by("month_region_city").sum().sort("month_region_city")
                    )
                    meta = (
                        df_vertical_concat.group_by("month_region_city").first().sort("month_region_city")
                    )
                    meta = meta.with_columns(total_requests=totals["total_requests"])
                    self.location = meta[
                        [time_unit, "countryCode", "regionName", "city", "total_requests"]
                    ]
        if not self.location.is_empty():
            df = self.location.sort(time_unit)
            dates = df[time_unit].unique().sort()

            for date in dates:
                df_sub = df.filter(pl.col(time_unit) == date)
                fn = output_dir / f"{str(date)}_aggregated_locations.csv"
                df_sub.write_csv(fn)
                if self.verbose:
                    print(f"write file {fn}")

    def undo_filter(self):
        """Reset to unfiltered DataFrame."""
        if self.verbose:
            print("Reset to unfiltered DataFrame")
        self.df = self.unfiltered_df
        self._update_original_total_requests()
