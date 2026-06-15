import os, shutil, glob
import numpy as np
import torch.multiprocessing as mp
from torch.utils.data import Dataset, DataLoader
from obspy import UTCDateTime
import config

# reloc config
cfg = config.Config()
ctlg_code = cfg.ctlg_code
dep_corr = cfg.dep_corr
num_grids = cfg.num_grids
num_workers = cfg.num_workers
keep_grids = cfg.keep_grids
hypo_root = cfg.hypo_root

# write hypoDD input file
def write_fin(i,j):
    fout = open('input/hypoDD_%s-%s.inp'%(i,j),'w')
    f=open('hypoDD.inp'); lines=f.readlines(); f.close()
    for line in lines:
        if 'event.dat' in line: line = 'input/event_%s-%s.dat \n'%(i,j)
        if 'hypoDD.reloc' in line: line = 'output/hypoDD_%s-%s.reloc \n'%(i,j)
        fout.write(line)
    fout.close()

def get_mag_dict():
    mag_dict = {}
    f=open('input/event.dat'); lines=f.readlines(); f.close()
    for line in lines:
        codes = line.split()
        evid = str(int(codes[-1]))
        mag = float(codes[5])
        mag_dict[evid] = mag
    return mag_dict

class Run_HypoDD(Dataset):
  """ Dataset for running HypoDD
  """
  def __init__(self, idx_list):
    self.idx_list = idx_list

  def __getitem__(self, index):
    # i/o paths
    i, j = self.idx_list[index]
    evid_list = evid_lists[i][j]
    out_ctlg = open('output/%s_%s-%s.ctlg'%(ctlg_code, i,j),'w')
    write_fin(i,j)
    # 3. run hypoDD
    os.system('%s/hypoDD input/hypoDD_%s-%s.inp > output/%s-%s.hypoDD'%(hypo_root,i,j,i,j))
    # 4. format output
    freloc = 'output/hypoDD_%s-%s.reloc'%(i,j)
    if not os.path.exists(freloc): 
        return
    f=open(freloc); lines=f.readlines(); f.close()
    for line in lines:
        codes = line.split()
        evid = codes[0]
        if int(evid) not in evid_list: continue
        # get loc info
        lat, lon, dep = codes[1:4]
        try: dep = round(float(dep) - dep_corr, 2)
        except: continue
        mag = mag_dict[evid]
        # get time info
        year, mon, day, hour, mnt, sec = codes[10:16]
        sec = '59.999' if sec=='60.000' else sec
        try: ot = UTCDateTime('{}{:0>2}{:0>2}{:0>2}{:0>2}{:0>6}'.format(year, mon, day, hour, mnt, sec))
        except: continue
        out_ctlg.write('{},{},{},{},{}\n'.format(ot, lat, lon, dep, mag))
    out_ctlg.close()

  def __len__(self):
    return len(self.idx_list)


if __name__ == '__main__':
    # 1. format fsta + assoc MFT det + grid event
    print('format input files')
    os.system('python mk_sta.py')
    os.system('python mk_dt.py')
    os.system('python mk_event.py')
    evid_lists = np.load('input/evid_lists.npy', allow_pickle=True)
    mag_dict = get_mag_dict()
    # 2. run hypoDD
    idx_list = [(i,j) for i in range(num_grids[0]) for j in range(num_grids[1])]
    dataset = Run_HypoDD(idx_list)
    dataloader = DataLoader(dataset, num_workers=num_workers, batch_size=None)
    for i, _ in enumerate(dataloader):
        print('run hypoDD: grid {0[0]}-{0[1]}'.format(idx_list[i]))
    os.unlink('hypoDD.log')
    # 3. merge output
    os.system('cat output/%s_*.ctlg > output/%s.ctlg'%(ctlg_code,ctlg_code))
    # delete grid files
    reloc_grids = glob.glob('output/hypoDD_[0-9]*-*[0-9].reloc*')
    ctlg_grids = glob.glob('output/%s_*.ctlg'%ctlg_code)
    input_files  = glob.glob('input/hypoDD_*.inp')
    input_files += glob.glob('input/event_*.dat') 
    if not keep_grids:
        for reloc_grid in reloc_grids: os.unlink(reloc_grid)
        for ctlg_grid in ctlg_grids: os.unlink(ctlg_grid)
        for input_file in input_files: os.unlink(input_file)
