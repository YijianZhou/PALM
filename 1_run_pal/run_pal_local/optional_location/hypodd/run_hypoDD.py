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

# read fpha with evid
def read_fpha():
    pha_dict, mag_dict = {}, {}
    f=open(cfg.fpha); lines=f.readlines(); f.close()
    for line in lines:
        codes = line.split(',')
        if len(codes[0])>=14:
            evid = codes[-1][:-1]
            pha_dict[evid] = []
            mag_dict[evid] = float(codes[4])
        else: pha_dict[evid].append(line)
    return pha_dict, mag_dict

# write hypoDD input file
def write_fin(i,j):
    fout = open('input/hypoDD_%s-%s.inp'%(i,j),'w')
    f=open('hypoDD.inp'); lines=f.readlines(); f.close()
    for line in lines:
        if 'dt.ct' in line: line = 'input/dt_%s-%s.ct \n'%(i,j)
        if 'event.dat' in line: line = 'input/event_%s-%s.dat \n'%(i,j)
        if 'hypoDD.reloc' in line: line = 'output/hypoDD_%s-%s.reloc \n'%(i,j)
        fout.write(line)
    fout.close()

def run_ph2dt():
    for i in range(num_grids[0]):
      for j in range(num_grids[1]):
        print('run ph2dt: grid %s-%s'%(i,j))
        shutil.copy('input/phase_%s-%s.dat'%(i,j), 'input/phase.dat')
        os.system('%s/ph2dt ph2dt.inp > output/%s-%s.ph2dt'%(hypo_root,i,j))
        os.system('mv event.sel event.dat dt.ct input')
        os.rename('input/dt.ct','input/dt_%s-%s.ct'%(i,j))
        os.rename('input/event.dat','input/event_%s-%s.dat'%(i,j))
        os.unlink('ph2dt.log')


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
    out_pha = open('output/%s_%s-%s.pha'%(ctlg_code, i,j),'w')
    out_pha_full = open('output/%s_%s-%s_full.pha'%(ctlg_code, i,j),'w')
    write_fin(i,j)
    # run hypoDD
    os.system('%s/hypoDD input/hypoDD_%s-%s.inp > output/%s-%s.hypoDD'%(hypo_root,i,j,i,j))
    # format output
    freloc = 'output/hypoDD_%s-%s.reloc'%(i,j)
    if not os.path.exists(freloc): return
    f=open(freloc); lines=f.readlines(); f.close()
    for line in lines:
        codes = line.split()
        evid = codes[0]
        if int(evid) not in evid_list: continue
        pha_lines = pha_dict[evid]
        mag = mag_dict[evid]
        # get loc info
        lat, lon, dep = codes[1:4]
        dep = round(float(dep) - dep_corr, 2)
        # get time info
        year, mon, day, hour, mnt, sec = codes[10:16]
        sec = '59.999' if sec=='60.000' else sec
        ot = UTCDateTime('{}{:0>2}{:0>2}{:0>2}{:0>2}{:0>6}'.format(year, mon, day, hour, mnt, sec))
        out_ctlg.write('{},{},{},{},{}\n'.format(ot, lat, lon, dep, mag))
        out_pha.write('{},{},{},{},{}\n'.format(ot, lat, lon, dep, mag))
        out_pha_full.write('{},{},{},{},{},{}\n'.format(ot, lat, lon, dep, mag, evid))
        for pha_line in pha_lines:
            out_pha.write(pha_line)
            out_pha_full.write(pha_line)
    out_ctlg.close()
    out_pha.close()
    out_pha_full.close()

  def __len__(self):
    return len(self.idx_list)


if __name__ == '__main__':
    # 1. format fpha & fsta
    print('format input files')
    pha_dict, mag_dict = read_fpha()
    os.system('python mk_sta.py')
    os.system('python mk_pha.py')
    evid_lists = np.load('input/evid_lists.npy', allow_pickle=True)
    # 2. run ph2dt
    run_ph2dt()
    # 3. run hypoDD
    idx_list = [(i,j) for i in range(num_grids[0]) for j in range(num_grids[1])]
    dataset = Run_HypoDD(idx_list)
    dataloader = DataLoader(dataset, num_workers=num_workers, batch_size=None)
    for i, _ in enumerate(dataloader):
        print('run hypoDD: grid {0[0]}-{0[1]}'.format(idx_list[i]))
    os.unlink('hypoDD.log')
    # 4. merge output
    os.system('cat output/%s_*.ctlg > output/%s.ctlg'%(ctlg_code,ctlg_code))
    os.system('cat output/%s_[0-9]*-*[0-9].pha > output/%s.pha'%(ctlg_code,ctlg_code))
    os.system('cat output/%s_*_full.pha > output/%s_full.pha'%(ctlg_code,ctlg_code))
    # delete grid files
    reloc_grids = glob.glob('output/hypoDD_[0-9]*-*[0-9].reloc*')
    ctlg_grids = glob.glob('output/%s_*.ctlg'%ctlg_code)
    pha_grids = glob.glob('output/%s_[0-9]*-[0-9]*.pha'%ctlg_code)
    input_files  = glob.glob('input/hypoDD_*.inp')
    input_files += glob.glob('input/phase_*.dat') 
    input_files += glob.glob('input/event_*.dat') 
    input_files += glob.glob('input/dt_*.ct') 
    if not keep_grids:
        for reloc_grid in reloc_grids: os.unlink(reloc_grid)
        for ctlg_grid in ctlg_grids: os.unlink(ctlg_grid)
        for pha_grid in pha_grids: os.unlink(pha_grid)
        for input_file in input_files: os.unlink(input_file)
