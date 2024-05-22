import numpy as np
import polars as pl
from collections import Counter
import matplotlib.pyplot as plt
import cartopy
import cartopy.crs as ccrs
import datetime
import matplotlib.dates as mdates
from pathlib import Path


def prep_for_plot(df):
    """
    Prepare DataFrame for plotting.

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
    df_parts = df['url'].to_pandas().str.replace(' ', '').str.split('?', expand=True)
    df = df.with_columns(base_url=df_parts[0].str.split('.', expand=True)[0].values)
    url_parts = df['base_url'].to_pandas().str.split('/', expand=True)
    url_parts['protocol'] = None
    url_parts.loc[url_parts[2] == 'tabledap', 'protocol'] = 'tabledap'
    url_parts.loc[url_parts[2] == 'griddap', 'protocol'] = 'griddap'
    url_parts.loc[url_parts[2] == 'files', 'protocol'] = 'files'
    url_parts.loc[url_parts[2] == 'info', 'protocol'] = 'info'
    url_parts['dataset_id'] = url_parts[3]
    df = df.with_columns(erddap_request_type=url_parts['protocol'].values)
    df = df.with_columns(dataset_id=url_parts['dataset_id'].values)
    df = (df
    .with_columns(
        dataset_id=pl.when(pl.col('erddap_request_type').is_null())
        .then(None)
        .otherwise(pl.col('dataset_id')))
    )
    df = df.with_columns(request_kwargs=df_parts[1].values)
    df = df.with_columns(file_type=df_parts[0].str.split('.', expand=True)[1].values)
    df = df.with_columns(
        user_agent_base=df['user-agent'].to_pandas().str.split(' ', expand=True)[0].str.split('/', expand=True)[
            0].values)
    ip_grid = df['ip'].to_pandas().str.split(".", expand=True)
    ip_group = ip_grid[0] + "." + ip_grid[1]
    ip_subnet = ip_grid[0] + "." + ip_grid[1] + "." + ip_grid[2]
    df = df.with_columns(ip_group=ip_group.values)
    df = df.with_columns(ip_subnet=ip_subnet.values)
    return df


def plot_daily_requests(df, num_days=7):
    """
    Plots a histogram of the number of requests per day.

    Parameters
    ----------
    df: polars.DataFrame
        DataFrame with requests information
    num_days: int, default=7
        number of days to be plotted
    """

    start = df.select("datetime").min()[0, 0]
    end = df.select("datetime").max()[0, 0]
    bins = np.arange(start, end, datetime.timedelta(days=num_days))
    fig, ax = plt.subplots(figsize=(12, 8))

    ax.hist(df.select("datetime"), bins=bins)

    # shade weekends
    xmin, xmax = ax.get_xlim()
    ymin, ymax = ax.get_ylim()
    days = np.arange(np.floor(xmin), np.ceil(xmax) + 2)
    weekends = [(dt.weekday() >= 5) | (dt.weekday() == 0) for dt in mdates.num2date(days)]
    ax.fill_between(days, *ax.get_ylim(), where=weekends, facecolor='k', alpha=0.1, zorder=-5)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)

    ax.set(xlim=(start, end), title=f"Requests per {num_days} days")
    plt.setp(ax.get_xticklabels(), rotation=45)


def _plot_popularity_bar(ax, df, col_name, rows):
    """
    Plot a horizontal histogram of the most repeated values of a specific column.

    Plot a horizontal histogram of the top (rows) most repeated values of (col_name).

    Parameters
    ----------
    ax: Axes
        Axes for the plot
    df: polars.DataFrame
        DataFrame with requests information
    col_name: str
        repeated values column name
    rows: int
        the number of bars in the histogram, each for a unique col_name

    Returns
    -------
    Axes
        Axes of the plot
    DataFrame
        subsetted DataFrame containing only the information to be plotted
    """
    counts = Counter(df[col_name].to_list()).most_common()
    if None in counts[0]:
        counts = counts[1:]
    df_counts = pl.DataFrame(counts)[:rows].fill_null("unknown").rename({'column_0': col_name, 'column_1': 'counts'})
    ax.barh(np.arange(len(df_counts)), df_counts['counts'].to_list(), tick_label=df_counts[col_name].to_list())
    ax.set_title(f"ERDDAP request most common {col_name}")
    ax.invert_yaxis()
    return ax, df_counts


def plot_most_popular(df, col_name='country', rows=10):
    """
    Wrapper around _plot_popularity_bar.

    Plots a horizontal histogram of the top (rows) most repeated values of col_name.

    Parameters
    ----------
    df: polars.DataFrame
        DataFrame with requests information
    col_name: str
        repeated values column name
    rows: int
        the number of bars in the histogram, each for a unique col_name

    Returns
    -------
    polars.DataFrame
        DataFrame with the (rows) most common (col_name) for plotting
    """
    if col_name not in df.columns:
        raise ValueError(f'supplied col_name {col_name} not found in df')
    fig, ax = plt.subplots(figsize=(8, 6))
    _, df_counts = _plot_popularity_bar(ax, df, col_name, rows)
    return df_counts


def plot_map_requests(df, aggregate_on='ip_group', extent=(-120, 40, 20, 70)):
    """
    Plot map with ip locations aggregated by a specific column.

    Parameters
    ----------
    df: polars.DataFrame
        DataFrame with requests information
    aggregate_on: str, default = 'ip_group'
        column name to be used as reference when aggregating data
    extent: tuple, default =(-120, 40, 20, 70)
        coordinates limits for map in (lonmin, lonmax, latmin, latmax) format

    """
    ip_group_info = df.group_by(aggregate_on).first()
    visits_by_ip_group = df.group_by(aggregate_on).len()
    ip_grouped = ip_group_info.join(visits_by_ip_group, on=aggregate_on, how="inner")
    ip_grouped = ip_grouped.filter(pl.col('len') > 10)

    pc = ccrs.PlateCarree()
    fig = plt.figure(figsize=(12, 10))
    ax = plt.axes(projection=ccrs.Mollweide())
    ax.set_extent(extent)
    ax.coastlines(zorder=-5)
    scale_fac = ip_grouped['len'].max() / 200
    plt.title("Visitors scaled by number of requests")
    ax.scatter(ip_grouped["lon"], ip_grouped["lat"], s=ip_grouped["len"] / scale_fac, transform=pc, color="C1")
    for i in [10, 100, 1000, 10000]:
        ax.scatter(1000, 10000, zorder=-10, s=i / scale_fac, label=i, transform=pc, color="C1")
    ax.legend()


def plot_bytes(df, days=3):
    """
    plot the total bytes sent by the server summed over a number of days

    Parameters
    ----------
    df: DataFrame
        DataFrame to be plotted
    days: int, default = 3
        Number of days to sum over

    """
    daily_bytes = df.group_by_dynamic("datetime", every=f"{days}d").agg(pl.col("bytes-sent").mean())
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.bar(daily_bytes['datetime'], daily_bytes['bytes-sent'])
    ax.set(ylabel=f'Bytes sent per {days} days')


def plot_for_single_ip(df_sub, fig_fn=None):
    """
    Plot and save graphs with request information from one single ip address.

    Parameters
    ----------
    df_sub: DataFrame
        Subsetted DataFrame to be plotted
    fig_fn: str, default = None
        Name of png file to be saved. If None, no figure is saved.

    """
    feature = cartopy.feature.NaturalEarthFeature(name='land', category='physical',
                                                  scale='50m', edgecolor='black', facecolor='lightgreen')
    ip = df_sub['ip'].to_list()[0]
    start = df_sub.select("datetime").min()[0, 0] -  datetime.timedelta(days=1)
    end = df_sub.select("datetime").max()[0, 0] + datetime.timedelta(days=1)
    bins = np.arange(start, end, datetime.timedelta(days=1))

    lon = df_sub.select("lon").mean()[0, 0]
    lat = df_sub.select("lat").mean()[0, 0]

    name = f'{df_sub.select("country")[0, 0]} {ip}'
    fig = plt.figure(figsize=(12, 8), layout="constrained")
    spec = fig.add_gridspec(6, 9)
    ax = fig.add_subplot(spec[:2, :4])
    ax.hist(df_sub.select("datetime"), bins=bins)

    # shade weekends if range is appropriate
    if datetime.timedelta(days=10) < end - start < datetime.timedelta(days=180):
        xmin, xmax = ax.get_xlim()
        ymin, ymax = ax.get_ylim()
        days = np.arange(np.floor(xmin), np.ceil(xmax) + 2)
        weekends = [(dt.weekday() >= 5) | (dt.weekday() == 0) for dt in mdates.num2date(days)]
        ax.fill_between(days, *ax.get_ylim(), where=weekends, facecolor='k', alpha=0.1, zorder=-5)

        ax.set_xlim(xmin, xmax)  # set limits back to default values
        ax.set_ylim(ymin, ymax)

    ax.set(xlim=(start, end), title=f"Requests per day {name}")
    plt.setp(ax.get_xticklabels(), rotation=45)

    coord = cartopy.crs.AzimuthalEquidistant(central_longitude=lon,
                                             central_latitude=lat)

    pc = ccrs.PlateCarree()
    ax1 = fig.add_subplot(spec[:3, 4:7], projection=coord)
    ax1.set_extent([lon - 20, lon + 20, lat - 20, lat + 20])
    ax1.add_feature(feature)
    ax1.scatter(lon, lat, transform=pc, color="C1", zorder=10)

    ax2 = fig.add_subplot(spec[:3, 7:])
    ax2.axis("off")
    i = 1
    ip_info = df_sub[0].to_dict()
    for key, val in ip_info.items():
        if key in ('country', 'regionName', 'city', 'zip', 'org', 'as'):
            i += 1
            ax2.text(0, -i, f"{key}: {val[0]}")
    for counter_var in ['user-agent', 'request_kwargs']:
        counter_val = Counter(df_sub[counter_var].drop_nulls().to_list()).most_common()
        if not counter_val:
            continue
        counter_val = counter_val[0][0]
        i += 1
        if len(counter_val) > 30:
            counter_val = counter_val[:30] + '...'
        ax2.text(0, -i, f"{counter_var}: {counter_val}")

    ax2.set(ylim=(-i - 1, -1))

    ax = fig.add_subplot(spec[3:, :4])
    _plot_popularity_bar(ax, df_sub, 'base_url', 10)

    ax = fig.add_subplot(spec[3:, 5:])
    _plot_popularity_bar(ax, df_sub, 'file_type', 10)
    if not Path("figs_by_ip").exists():
        Path("figs_by_ip").mkdir()
    if fig_fn:
        fig.savefig(f"figs_by_ip/{fig_fn}.png")