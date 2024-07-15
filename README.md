# erddaplogs

[![Project Status: Active – The project has reached a stable, usable state and is being actively developed.](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active)

![python versions](https://img.shields.io/pypi/pyversions/erddaplogs.svg)

![pypi](https://badge.fury.io/py/erddaplogs.svg) ![anaconda badge](https://anaconda.org/conda-forge/erddaplogs/badges/version.svg) 


A package for analysing traffic to an ERDDAP server by parsing nginx and apache logs.

Try it out on Binder [![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/callumrollo/erddaplogs/HEAD?labpath=weblogs-parse-demo.ipynb)

### Installation

* #### From pypi, using pip

```sh
pip install erddaplogs
```

#### From conda-forge

```sh
conda install -c conda-forge erddaplogs
```

* #### From the repo, using pip

```sh
#First, clone the repo:
git clone https://github.com/callumrollo/erddaplogs.git
cd erddaplogs
pip install -r requirements-dev.txt # install the dependencies
pip install -e .
```

### Example usage


First, get the logs copied locally to a directory you can read and unzip them. e.g.:

```bash
rsync /var/log/nginx/* logs
gzip -dfr * logs
```
Next, run erddaplogs

```python
from erddaplogs.logparse import ErddapLogParser

parser = ErddapLogParser()
parser.load_nginx_logs("example_data/nginx_example_logs/") # replace with the path to your logs
parser.parse_datasets_xml("example_data/datasets.xml") # replace with the path to your xml, or remove this line
parser.temporal_resolution = 'month' # can be any one of 'day', 'month' or 'year'. Defaults to 'month'
parser.filter_non_erddap()
parser.filter_spam()
parser.filter_locales()
parser.filter_user_agents()
parser.filter_common_strings()
parser.get_ip_info() # fetches info on ip addresses
parser.filter_organisations()
parser.parse_columns()
parser.export_data(output_dir=".") # Put the path to the output dir here. Preferably somewhere your ERDDAP can read
```

This will read nginx logs from the user specified directory and write files `<timestamp>_anonymized_requests.csv` and `<timestamp>_aggregated_locations.csv` with anonymized requests and aggregated location data respectively. 

`timestamp` will be YYYY YYYY-MM or YYYY-MM-DD depending on whether the user has set `temporal_resolution` to year, month or day. 

ErddapLogParser can be run on a static directory of logs as a cron job e.g. once per day. If run repeatedly, it will create a new files for `anonymized_requests` and `aggregated_locations` using only requests that have been received since the last timestamp (by default, the first day of the current month).

To re-analyze all the input requests, first delete the output files in `output_dir` then re-run.

All of the `filter_` functions are optional, and most take additional kwargs to fine-tune their behaviour.

### Share results via ERDDAP

Optionally, the resulting anonymized data can be shared on your ERDDAP in two datasets `requests` and `locations`. To do this, add the contents of the example xml files `requests.xml` and `locations.xml` from the `example_data` directory to your `datasets.xml`. Make sure to update the values of **fileDir**, **institution** and change the date variable if not using the default monthly aggregation. The other fields can remain as-is.

You can see what the resulting stats look like on the VOTO ERDDAP server:

- https://erddap.observations.voiceoftheocean.org/erddap/tabledap/requests.html
- https://erddap.observations.voiceoftheocean.org/erddap/tabledap/locations.html
 
For more analysis options and plots, see the example jupyter notebook

### Example Jupyter Notebook

You can find an example Jupyter Notebook `weblogs-parse-demo.ipynb` in the `notebooks` directory. It performs the following steps:

1. Reads in apache and nginx logs, combine them into one consistent dataframe
2. Find the ips that made the greatest number of requests. Get their info from ip-api.com
3. Remove suspected spam/bot requests
4. Classify user data by identifying user agents, matching requets to dataset type etc.
5. Perform basic analysis to graph number of requests and users over time, most popular datasets/datatypes and geographic distribution of users
6. Anonymize user data and write them to file

A rather out od date blog post explaining this notebook in more detail can be found at [https://callumrollo.com/weblogparse.html](https://callumrollo.com/weblogparse.html)

A second notebook called `analyze_anonymized_usage` shows some examples of plotting the anonymized datasets made available on the VOTO ERDDAP

### A note on example data

If you don't have your own ERDDAP logs to hand, you can use the example data in `example_data/nginx_example_logs`. This is anonymized data from a production ERDDAP server [erddap.observations.voiceoftheocean.org](https://erddap.observations.voiceoftheocean.org/erddap). The ip addresses have been randomly generated, as have the user agents. All subscription emails have been replaced with fake@example.com

### Logging best practices

- The log loading function can be run sequentially over a series of directories if you have logs in sevaral places.
- You must retain logs for at least as long as your temporal resolution! If you only retain logs for 3 days, but aggregate data by month, some data will be lost.
- The default of many servers is to delete logs after a short period of time. Check the settings of logrotate in e.g. `/etc/logrotate.d/nginx`
- Check your institution's policies on log retention
- If you set a very fine temporal resolution like `day` on a server that receives little traffic, you may make enable partial re-identification, linking e.g. the full request url with the city/region of the user who sent it.

### License

This project is licensed under MIT.
