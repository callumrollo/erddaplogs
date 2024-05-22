from copy import copy
from apachelogs import LogParser
from pathlib import Path
import polars as pl
from collections import Counter
from user_agents import parse
import requests
import re
import gzip


def _load_apache_logs(apache_logs_dir):
    apache_logs = list(Path(apache_logs_dir).glob("*access.log*"))
    if len(apache_logs) == 0:
        raise ValueError(
            f"Supplied directory {apache_logs_dir} contains no access.log files",
        )
    parser = LogParser("%h %l %u %t \"%r\" %>s %b \"%{Referer}i\" \"%{User-Agent}i\"")
    dt, ip, url, ua, code, bytes_sent, referer = [], [], [], [], [], [], []
    for fn in apache_logs:
        with open(fn) as fp:
            for entry in parser.parse_lines(fp):
                try:
                    this_url = entry.request_line.split(" ")[1]
                except:
                    this_url = ""
                dt.append(entry.request_time)
                ip.append(entry.remote_host)
                url.append(this_url)
                ua.append(entry.headers_in["User-Agent"])
                code.append(entry.final_status)
                bytes_sent.append(entry.bytes_sent)
                referer.append(entry.headers_in['Referer'])
    df = pl.DataFrame({"ip": ip, "datetime": dt, "url": url, "user-agent": ua, "status-code": code,
                       'bytes-sent': bytes_sent, 'referer': referer}).with_columns(pl.col("datetime").dt.replace_time_zone(None))
    df = df.with_columns(pl.col('status-code').cast(pl.Int64))
    df = df.with_columns(pl.col('bytes-sent').cast(pl.Int64))
    return df


def _load_nginx_logs(nginx_logs_dir):
    # nginx log parser from https://gist.github.com/hreeder/f1ffe1408d296ce0591d
    csvs = list(Path(nginx_logs_dir).glob("tomcat-access.log*"))
    if len(csvs) == 0:
        raise ValueError(
            f"Supplied directory {nginx_logs_dir} contains no tomcat-access.log files",
        )
    lineformat = re.compile(
        r"""(?P<ipaddress>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}) - - \[(?P<dateandtime>\d{2}/[a-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} ([+\-])\d{4})] ((\"(GET|POST|HEAD|PUT|DELETE) )(?P<url>.+)(http/(1\.1|2\.0)")) (?P<statuscode>\d{3}) (?P<bytessent>\d+) (?P<refferer>-|"([^"]+)") (["](?P<useragent>[^"]+)["])""",
        re.IGNORECASE)
    ip, datetimestring, url, bytessent, referer, useragent, status, method = [], [], [], [], [], [], [], []
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
        {"ip": ip, "datetime": datetimestring, "url": url, "user-agent": useragent, "status-code": status,
         'bytes-sent': bytessent, 'referer': referer})
    df = df.with_columns(pl.col('status-code').cast(pl.Int64))
    df = df.with_columns(pl.col('bytes-sent').cast(pl.Int64))
    # convert timestamp to datetime
    df = df.with_columns(
        pl.col("datetime").str.strptime(pl.Datetime, format="%d/%b/%Y:%H:%M:%S +0000").dt.replace_time_zone(None))
    df_nginx = df.sort(by="datetime")
    return df_nginx


def _get_ip_info(df, ip_info_csv, download_new=True, verbose=False):
    ip_counts = Counter(df['ip']).most_common()
    if Path(ip_info_csv).exists():
        df_ip = pl.read_csv(ip_info_csv)
    else:
        df_ip = pl.DataFrame({'status': '',
                              'country': '',
                              'countryCode': '',
                              'region': '',
                              'regionName': '',
                              'city': '',
                              'zip': '',
                              'lat': 0.0,
                              'lon': 0.0,
                              'timezone': '',
                              'isp': '',
                              'org': '',
                              'as': '',
                              'query': ''})
    if download_new:
        for ip, count in ip_counts:
            if ip not in df_ip["query"]:
                resp_raw = requests.get(f"http://ip-api.com/json/{ip}")
                if resp_raw.status_code == 429:
                    print("Exceeded API responses. Wait a minute and try again")
                    break
                resp = resp_raw.json()
                if verbose:
                    if 'country' in resp.keys():
                        print(f"New ip identified: {ip} in {resp['country']}. Sent {count} requests")
                    else:
                        print(f"New ip identified: {ip}. Sent {count} requests")
                try:
                    df_ip = pl.concat((df_ip, pl.DataFrame(resp)), how="diagonal")
                except:
                    print(f"Issue with schema for this ip address {ip}, skipping")
    df_ip.write_csv(ip_info_csv)
    if verbose:
        print(f"We have info on {len(df_ip)} ip address")
    return df_ip


def _print_filter_stats(call_wrap):
    def magic(self):
        len_before = len(self.df)
        call_wrap(self)
        if self.verbose:
            print(f"Filter {self.filter_name} dropped {len_before - len(self.df)} lines. Length of dataset is now "
                  f"{int(len(self.df) / self.original_total_requests * 100)} % of original")
    return magic


class ErddapLogParser:
    def __init__(self):
        self.df = pl.DataFrame()
        self.ip = pl.DataFrame()
        self.verbose = False
        self.original_total_requests = 0
        self.filter_name = None

    def _update_original_total_requests(self):
        self.original_total_requests = len(self.df)
        self.unfiltered_df = copy(self.df)
        if self.verbose:
            print(f'DataFrame now has {self.original_total_requests} lines')

    def subset_df(self, rows=1000):
        stride = int(self.df.shape[0] / rows)
        if self.verbose:
            print(f'starting from DataFrame with {self.df.shape[0]} lines. Subsetting by a factor of {stride}')
        self.df = self.df.gather_every(stride)
        if self.verbose:
            print('resetting number of original total requests to match subset DataFrame')
        self._update_original_total_requests()

    def load_apache_logs(self,
                         apache_logs_dir: str):
        df_apache = _load_apache_logs(apache_logs_dir)
        if self.verbose:
            print(f'loaded {len(df_apache)} log lines from {apache_logs_dir}')
        df_combi = pl.concat(
            [
                self.df,
                df_apache,
            ],
            how="vertical", )
        df_combi = df_combi.sort("datetime").unique()
        self.df = df_combi
        self._update_original_total_requests()

    def load_nginx_logs(self,
                        nginx_logs_dir: str):
        df_nginx = _load_nginx_logs(nginx_logs_dir)
        if self.verbose:
            print(f'loaded {len(df_nginx)} log lines from {nginx_logs_dir}')
        df_combi = pl.concat(
            [
                self.df,
                df_nginx,
            ],
            how="vertical", )
        df_combi = df_combi.sort("datetime").unique()
        self.df = df_combi
        self._update_original_total_requests()

    def get_ip_info(self,
                    ip_info_csv="ip.csv",
                    download_new=True):
        if "country" in self.df.columns:
            return
        df_ip = _get_ip_info(self.df, ip_info_csv, download_new=download_new, verbose=self.verbose)
        self.ip = df_ip
        self.df = self.df.join(df_ip, left_on='ip', right_on='query').sort("datetime")

    @_print_filter_stats
    def filter_non_erddap(self):
        """Filter out non-genuine requests."""
        self.filter_name = "non erddap"
        self.df = self.df.filter(
            pl.col("url").str.contains("erddap")
        )

    @_print_filter_stats
    def filter_organisations(self,
                             organisations=("Google", "Crawlers", "SEMrush")):
        """Filter out non-visitor requests from specific organizations."""
        if 'org' not in self.df.columns:
            raise ValueError(
                f"Organisation information not present in DataFrame. Try running get_ip_info first.",
            )
        self.df = self.df.with_columns(pl.col("org").fill_null("unknown"))
        self.df = self.df.with_columns(pl.col("isp").fill_null("unknown"))
        for block_org in organisations:
            self.df = self.df.filter(
                ~pl.col("org").str.contains(f"(?i){block_org}")
            )
            self.df = self.df.filter(
                ~pl.col("isp").str.contains(f"(?i){block_org}")
            )
        self.filter_name = "organisations"

    @_print_filter_stats
    def filter_user_agents(self):
        """Filter out requests from bots."""
        # Added by Samantha Ouertani at NOAA AOML Jan 2024
        self.df = self.df.filter(
            ~pl.col("user-agent").map_elements(lambda ua: parse(ua).is_bot)
        )
        self.filter_name = "user agents"

    @_print_filter_stats
    def filter_locales(self, locales=("zh-CN", "zh-TW", "ZH")):
        # Added by Samantha Ouertani at NOAA AOML Jan 2024
        """Filter out requests from specific regions (locales)."""
        for locale in locales:
            self.df = self.df.filter(
                ~pl.col("url").str.contains(f"{locale}")
            )
        self.filter_name = 'locales'

    @_print_filter_stats
    def filter_spam(self,
                    spam_strings=(".env", "env.", ".php", ".git", "robots.txt", "phpinfo", "/config", "aws", ".xml")
                    ):
        """
        Filter out requests from non-visitors.

        Filter out requests from indexing webpages, services monitoring uptime,
        requests for files that aren't on the server, etc
        """
        page_counts = Counter(list(self.df.select("url").to_numpy()[:, 0])).most_common()
        bad_pages = []
        for page, count in page_counts:
            for phrase in spam_strings:
                if phrase in page:
                    bad_pages.append(page)
        self.df = self.df.filter(~pl.col('url').is_in(bad_pages))
        self.filter_name = 'spam'

    @_print_filter_stats
    def filter_files(self):
        """Filter out requests for browsing erddap's virtual file system."""
        # Added by Samantha Ouertani at NOAA AOML Jan 2024
        self.df = self.df.filter(
            ~pl.col("url").str.contains("/files")
        )
        self.filter_name = 'files'

    @_print_filter_stats
    def filter_common_strings(self, strings=('/version', 'favicon.ico', '.js', '.css', '/erddap/images')):
        """Filter out non-data requests - requests for version, images, etc"""
        for string in strings:
            self.df = self.df.filter(
                ~pl.col("url").str.contains(string)
            )
        self.filter_name = 'common strings'

    @_print_filter_stats
    def filter_anonymize_user_agent(self):
        self.df = self.df.with_columns(pl.col("user-agent").map_elements(lambda ua: parse(ua).browser.family, return_dtype=pl.String).alias("BrowserFamily"))
        self.df = self.df.with_columns(pl.col("user-agent").map_elements(lambda ua: parse(ua).device.family, return_dtype=pl.String).alias("DeviceFamily"))
        self.df = self.df.with_columns(pl.col("user-agent").map_elements(lambda ua: parse(ua).os.family, return_dtype=pl.String).alias("OS"))
        self.df = self.df.drop("user-agent")
        self.filter_name = 'anonymize_user_agent'

    def undo_filter(self):
        if self.verbose:
            print(f'Reset to unfiltered DataFrame')
        self.df = self.unfiltered_df
        self._update_original_total_requests()
