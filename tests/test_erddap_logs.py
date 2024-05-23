import polars as pl
from erddaplogs.logparse import ErddapLogParser
import erddaplogs.plot_functions as plot_functions


def test_parser():
    parser = ErddapLogParser()
    nginx_logs_dir = "example_data/nginx_example_logs/"
    parser.load_nginx_logs(nginx_logs_dir)
    parser.subset_df(1000)
    parser.filter_non_erddap()
    parser.filter_spam()
    parser.filter_locales()
    parser.filter_user_agents()
    parser.filter_common_strings()
    assert parser.df.shape > (500, 5)
    parser.get_ip_info(num_ips=3)
    assert parser.df.shape > (300, 20)
    parser.filter_organisations()
    parser.parse_datasets_xml("example_data/datasets.xml")
    parser.parse_columns()
    assert len(parser.df['dataset_type'].unique()) > 2
    parser.df.write_parquet("example_data/df_example.pqt")


def test_anonymized_data():
    parser = ErddapLogParser()
    parser.df = pl.read_parquet("example_data/df_example.pqt").sort(by="datetime")
    parser.export_data()
    assert "email=" not in "".join(parser.anonymized['url'].to_list())
    for blocked_col in ["user-agent", "lat", "lon", "org", "zip", "city"]:
        assert blocked_col not in parser.anonymized.columns
    assert parser.anonymized['ip'].dtype == pl.Int32
    assert parser.location.columns == ['countryCode', 'regionName', 'city', 'len']


def test_plots():
    df = pl.read_parquet("example_data/df_example.pqt").sort(by="datetime")
    plot_functions.plot_daily_requests(df, num_days=1)
    plot_functions.plot_bytes(df)
    plot_functions.plot_most_popular(df, col_name='dataset_id')
    plot_functions.plot_map_requests(df, aggregate_on='ip_subnet')
    dfa = plot_functions.plot_most_popular(df, col_name='ip', rows=2)
    for rank, ip in enumerate(dfa['ip'].to_list()):
        df_sub = df.filter(pl.col('ip') == ip)
        plot_functions.plot_for_single_ip(df_sub, f'visitor_rank_{rank}_ip_{ip}')
