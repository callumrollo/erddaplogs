# erddaplogs

[![Project Status: Active – The project has reached a stable, usable state and is being actively developed.](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active)

![python versions](https://img.shields.io/pypi/pyversions/erddaplogs.svg)

![pypi](https://badge.fury.io/py/erddaplogs.svg)


A package for analysing traffic to an ERDDAP server by parsing nginx and apache logs.

Try it out on Binder [![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/callumrollo/erddaplogs/HEAD?labpath=weblogs-parse-demo.ipynb)

### Installation

* #### From pypi, using pip

```sh
pip install erddaplogs
```

* #### From the repo, using conda:

```sh 
git clone https://github.com/callumrollo/erddaplogs.git # clone the repo
conda env create --file environment.yml # create the environment 
conda activate erddaplogs # activate the environment
```

* #### From the repo, using pip

```sh
#First, clone the repo:
git clone https://github.com/callumrollo/erddaplogs.git

# Set up your environment directory to the path of your choice.
# Here we'll use ~/virtualenvs/erddaplogs.  
# Any parent directories should already exist.
venv ~/virtualenvs/erddaplogs # create the environment
. ~/virtualenvs/erddaplogs/bin/activate # activate the environment

# go to the directory of the repo, as install the dependencies 
# as user:
pip install -r requirements.txt # install the dependencies
# or as a developer, if you plan on contributing to the project:
pip install -r requirements-dev.txt # install the dependencies
pip install .
```

* #### with pip, locally

```sh 
# First, clone the repo:
git clone https://github.com/callumrollo/erddaplogs.git 
# then, inside the repo, execute
python3 -m pip install -e .
```
### Example usage

```python
from erddaplogs.logparse import ErddapLogParser

parser = ErddapLogParser()
parser.load_nginx_logs("example_data/nginx_example_logs/")
parser.parse_datasets_xml("example_data/datasets.xml")
parser.filter_non_erddap()
parser.filter_spam()
parser.filter_locales()
parser.filter_user_agents()
parser.filter_common_strings()
parser.get_ip_info()
parser.filter_organisations()
parser.parse_columns()
parser.export_data()
```

This will read nginx logs from the user specified directory and write two files `<timestamp>anonymized.csv` and `<timestamp>location.csv` with anonymised user data. For more analysis options and plots, see the example jupyter notebook

### Example Jupyter Notebook

You can find an example Jupyter Notebook 
[here](https://github.com/callumrollo/erddaplogs/blob/main/weblogs-parse-demo.ipynb). It performs the following steps:

1. Read in apache and nginx logs, combine them into one consistent dataframe
2. Find the ips that made the greatest number of requests. Get their info from ip-api.com
3. Remove suspected spam/bot requests
4. Perform basic anaylysis to graph number of requests and users over time, most popular datasets/datatypes and geographic distribution of users

A blog post explaining this notebook in more detail can be found at [https://callumrollo.com/weblogparse.html](https://callumrollo.com/weblogparse.html)

### A note on example data

If you don't have your own ERDDAP logs to hand, you can use the example data in `example_data/nginx_example_logs`. This is anonymmised data from a production ERDDAPP server [erddap.observations.voiceoftheocean.org](https://erddap.observations.voiceoftheocean.org/erddap). The ip addresses have been randommly generated, as have the user agents. All subscription emails have been replaced with fake@example.com


### License

This project is licensed under MIT.
