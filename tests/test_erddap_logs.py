from erddaplogs.logparse import ErddapLogParser


def test_parser():
    parser = ErddapLogParser()
    nginx_logs_dir = "example_data/nginx_example_logs/"
    parser.load_nginx_logs(nginx_logs_dir)
    parser.filter_non_erddap()
    parser.filter_spam()
    parser.filter_locales()
    parser.filter_user_agents()
    parser.filter_common_strings()
    parser.get_ip_info(download_new=True)
    parser.filter_organisations()
    parser.export_data()
