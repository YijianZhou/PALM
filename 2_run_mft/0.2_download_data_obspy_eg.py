""" Download FDSN data with obspy MassDownloader
"""
import os
from obspy import UTCDateTime
from obspy.clients.fdsn.mass_downloader import RectangularDomain, Restrictions, MassDownloader

# i/o paths
data_root = '/data/Example_data'
sta_dir = 'output/eg_stations'
# down params
providers = ["SCEDC"]
chn_codes = ['HH*','EH*']
loc_codes = ["*"]
num_workers = 10
start_date, end_date = UTCDateTime('20190704'), UTCDateTime('20190707')
lat_rng = [35.25,36.25]
lon_rng = [-118,-117,1]
num_day = int((end_date - start_date) / 86400) + 1
print('data range:')
print('latitude range: %s'%(lat_rng))
print('longitude range: %s'%(lon_rng))
print('time range: %s'%[start_date, end_date])

domain = RectangularDomain(minlatitude=lat_rng[0], maxlatitude=lat_rng[1],
                           minlongitude=lon_rng[0], maxlongitude=lon_rng[1])
for day_idx in range(num_day):
    t0 = start_date + 86400*day_idx
    t1 = start_date + 86400*(day_idx+1)
    print('downloading %s'%t0)
    # 1. set domain & restrict
    restrict = Restrictions(
        starttime=t0, endtime=t1,
        network="*", station="*", 
        reject_channels_with_gaps=False,
        minimum_length=0.0,
        minimum_interstation_distance_in_m=10,
        channel_priorities=chn_codes,
        location_priorities=loc_codes)
    # 2. set storage
    out_dir = os.path.join(data_root, ''.join(str(t0.date).split('-')))
    if not os.path.exists(out_dir): os.makedirs(out_dir)
    # 3. start download
    mdl = MassDownloader(providers=providers)
    mdl.download(domain, restrict, 
        threads_per_client=num_workers, mseed_storage=out_dir,
        stationxml_storage=sta_dir)

