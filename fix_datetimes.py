import pandas as pd
import numpy as np

INPUT_PATH = 'data/delta_ontime_clean.csv'
OUTPUT_PATH = 'data/delta_ontime_with_datetimes.csv'


def combine_date_time(date, hhmm, anchor=None):
    """Combine a date with a BTS HHMM float into a Timestamp.

    Handles the BTS '2400 means midnight at the end of the day' quirk and
    NaN passthrough (cancelled/missing legs). If `anchor` is given, the
    event is placed on `anchor`'s resolved calendar date (so day bumps from
    earlier events in the chain carry forward), bumped one further day if
    this event's clock-time is earlier than `anchor`'s clock-time (midnight
    rollover relative to the prior event). Without an anchor, the event is
    placed directly on `date`.
    """
    date = pd.to_datetime(date, errors='coerce')
    hhmm = pd.to_numeric(hhmm, errors='coerce')

    is_2400 = hhmm == 2400
    hour = (hhmm // 100).where(~is_2400, 0).astype('Int64')
    minute = (hhmm % 100).where(~is_2400, 0).astype('Int64')

    if anchor is None:
        # No prior event to compare against: 2400 is the only source of a
        # day bump (e.g. CRSDepDatetime/DepDatetime, anchored to FlightDate).
        base_date = date.dt.normalize() + pd.to_timedelta(is_2400.astype(int), unit='D')
    else:
        # clock resolves to 0000 for 2400, which is already "earlier than"
        # any anchor clock > 0000, so the rollover check below subsumes the
        # 2400 case here - adding an extra explicit bump would double-count.
        clock = hour * 100 + minute
        anchor_clock = (anchor.dt.hour * 100 + anchor.dt.minute).astype('Int64')
        rollover = (clock < anchor_clock).fillna(False)
        base_date = anchor.dt.normalize() + pd.to_timedelta(rollover.astype(int), unit='D')

    return (
        base_date
        + pd.to_timedelta(hour, unit='h')
        + pd.to_timedelta(minute, unit='m')
    )


def fix_datetimes(df):
    df['CRSDepDatetime'] = combine_date_time(df['FlightDate'], df['CRSDepTime'])
    df['CRSArrDatetime'] = combine_date_time(df['FlightDate'], df['CRSArrTime'], anchor=df['CRSDepDatetime'])

    df['DepDatetime'] = combine_date_time(df['FlightDate'], df['DepTime'])
    df['ArrDatetime'] = combine_date_time(df['FlightDate'], df['ArrTime'], anchor=df['DepDatetime'])

    df['WheelsOffDatetime'] = combine_date_time(df['FlightDate'], df['WheelsOff'], anchor=df['DepDatetime'])
    df['WheelsOnDatetime'] = combine_date_time(df['FlightDate'], df['WheelsOn'], anchor=df['WheelsOffDatetime'])

    return df


def main():
    df = pd.read_csv(INPUT_PATH, low_memory=False)
    df = fix_datetimes(df)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f'Wrote {len(df):,} rows to {OUTPUT_PATH}')


if __name__ == '__main__':
    main()
