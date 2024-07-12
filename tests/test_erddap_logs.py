import polars as pl
from erddaplogs.logparse import ErddapLogParser
import erddaplogs.plot_functions as plot_functions
import os
import shutil
from pathlib import Path
cwd = Path(os.getcwd())


def remove_processed_files(tgt=cwd):
    existing_output_files = list(tgt.glob("*anonymized_requests.csv")) + list(tgt.glob("*aggregated_locations.csv"))
    if len(existing_output_files) > 0:
        for old_file in existing_output_files:
            os.unlink(str(old_file))


for sub_name in ["sub_0", "sub_1"]:
    sub_dir = Path("example_data/nginx_example_logs") / sub_name
    if not sub_dir.exists():
        sub_dir.mkdir()
for infile in Path("example_data/nginx_example_logs/").glob("*access*"):
    fn = infile.name
    if int(fn[-1]) >= 5:
        shutil.copy(f"example_data/nginx_example_logs/{fn}", f"example_data/nginx_example_logs/sub_0/{fn}")
    shutil.copy(f"example_data/nginx_example_logs/{fn}", f"example_data/nginx_example_logs/sub_1/{fn}")


def test_parser():
    remove_processed_files()
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
    df = parser.df
    assert len(df['dataset_type'].unique()) > 2
    assert df['erddap_request_type'].is_null().sum() / df.shape[0] < 0.01
    assert 0.2 < df['dataset_id'].is_null().sum() / df.shape[0] < 0.3
    df.write_parquet("example_data/df_example.pqt")


def test_sequential_process():
    out = Path("example_output_days")
    remove_processed_files(tgt=out)
    for sub_dir in ["sub_0", "sub_1"]:
        parser = ErddapLogParser()
        parser.temporal_resolution = 'day'
        nginx_logs_dir = f"example_data/nginx_example_logs/{sub_dir}"
        parser.load_nginx_logs(nginx_logs_dir)
        parser.get_ip_info(num_ips=3)
        parser.parse_datasets_xml("example_data/datasets.xml")
        parser.parse_columns()
        parser.export_data(output_dir=out)

    assert len(list(out.glob("*aggregated_locations.csv"))) == 9
    assert len(list(out.glob("*anonymized_requests.csv"))) == 9
    
    out = Path("example_output_months")
    remove_processed_files(tgt=out)
    for sub_dir in ["sub_0", "sub_1"]:
        parser = ErddapLogParser()
        nginx_logs_dir = f"example_data/nginx_example_logs/{sub_dir}"
        parser.load_nginx_logs(nginx_logs_dir)
        parser.get_ip_info(num_ips=3)
        parser.parse_datasets_xml("example_data/datasets.xml")
        parser.parse_columns()
        parser.export_data(output_dir=out)

    df_days = pl.read_csv("example_output_days/*anonymized_requests.csv")
    df_month = pl.read_csv("example_output_months/*anonymized_requests.csv")
    assert df_days.shape == df_month.shape
    df_days_loc = pl.read_csv("example_output_days/*aggregated_locations.csv")
    df_month_loc = pl.read_csv("example_output_months/*aggregated_locations.csv")
    assert df_days_loc['total_requests'].sum() == df_month_loc['total_requests'].sum()


def test_anonymized_data():
    parser = ErddapLogParser()
    parser.df = pl.read_parquet("example_data/df_example.pqt").sort(by="datetime")
    parser.export_data()
    assert "email=" not in "".join(parser.anonymized['url'].to_list())
    for blocked_col in ["user_agent", "lat", "lon", "org", "zip", "city"]:
        assert blocked_col not in parser.anonymized.columns
    assert parser.anonymized['ip_id'].dtype == pl.String
    assert not set(parser.location.columns).difference(['month', 'countryCode', 'regionName', 'city', 'total_requests'])


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
