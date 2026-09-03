""" Make input station file for hypoInverse
"""
import config

# i/o paths
cfg = config.Config()
fsta = cfg.fsta
fout = open('input/station.dat','w')
lat_code = cfg.lat_code
lon_code = cfg.lon_code

done_list = []
f=open(fsta); lines=f.readlines(); f.close()
for line in lines:
    codes = line.split(',')
    if codes[0] in done_list: continue
    done_list.append(codes[0])
    net, sta = codes[0].split('.')
    lat, lon, ele = [float(code) for code in codes[1:4]]
    lat, lon, ele = abs(lat), abs(lon), int(ele)
    lat_deg = int(lat)
    lat_min = 60*(lat-int(lat))
    lon_deg = int(lon)
    lon_min = 60*(lon-int(lon))
    lat = '{:2} {:7.4f}{}'.format(lat_deg, lat_min, lat_code)
    lon = '{:3} {:7.4f}{}'.format(lon_deg, lon_min, lon_code)
    # hypoinverse format 2
    fout.write("{:<5} {}  HHZ  {}{}{:4}\n".format(sta, net, lat, lon, ele))
fout.close()
