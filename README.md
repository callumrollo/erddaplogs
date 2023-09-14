# website-log-parse
Quick utilities for parsing nginx and apache logs.

This script takes apache and/or nginx logs as input. It is made to analyse visitors to an ERDDAP server, but sohuld work on any web traffic.

The jupyter notebook performs the following steps:
1. Read in apache and nginx logs, combine them into one consistent dataframe
2. Find the ips that made the greatest number of requests. Get their info from ip-api.com
3. Remove suspected spam/bot requests
4. Perform basic anaylysis to graph number of requests and users over time, most popular datasets/datatypes and geographic distribution of users

This project is licensed under MIT. It alsmost certainly contains errors!
