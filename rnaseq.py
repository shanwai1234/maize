#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import os.path as op
import numpy as np
import sys
import time
import argparse
import itertools
import luigi

def run_cmds_ssh(cmds):
    username = 'zhoup'
    hostname = 'mesabi.msi.umn.edu'
    port = 22
    pubkey = os.path.join(os.environ['HOME'], '.ssh', 'id_rsa')
    
    key = paramiko.RSAKey.from_private_key_file(pubkey)
    s = paramiko.SSHClient()
    s.load_system_host_keys()
    s.connect(hostname, port, pkey=key)
    for cmd in cmds:
        stdin, stdout, stderr = s.exec_command(cmd)
        for line in stdout:
            print "... " + line.strip("\n")
    s.close()

class WaitJob(luigi.ExternalTask):
    fc = luigi.Parameter()
    def output(self):
        return luigi.LocalTarget(self.fc)
class rnaseq0Sra(luigi.Task):
    pid = luigi.Parameter()
    name = luigi.Parameter(default="rnaseq0Sra")
    def run(self):
        pid, name = self.pid, self.name
        dirw = op.join(os.environ['misc2'], 'rnaseq', pid)
        if not op.isdir(dirw): os.makedirs(dirw)
        os.chdir(dirw)
        if not op.isdir("cps"): os.makedirs("cps")
        assert op.isfile("01.srr.tsv"), "no 01.srr.tsv in %s" % dirw
        ary = np.genfromtxt("01.srr.tsv", names = True, dtype = None, delimiter = "\t")
        cols = list(ary.dtype.names)
        if not op.isdir("03.sra"): os.makedirs("03.sra")
        if not op.isdir("05.reads"): os.makedirs("05.reads")
        fhc = open("04.fastqdump.sh", "w")
        d03 = op.abspath("03.sra")
        d05 = op.abspath("05.reads")
        d03 = "03.sra"
        d05 = "05.reads"
        os.chdir("03.sra")
        tmpi_dir = "/scratch.global/zhoux379"
        tmpo_dir = "/scratch.global/zhoux379.out"
        if not op.isdir(tmpi_dir): os.makedirs(tmpi_dir)
        if not op.isdir(tmpo_dir): os.makedirs(tmpo_dir)
        for row in ary:
            rid = row[0]
            print rid
            cmd = "wget ftp://ftp-trace.ncbi.nih.gov/sra/sra-instant/reads/ByRun/sra/%s/%s/%s/%s.sra" % (rid[0:3], rid[0:6], rid, rid)
            #os.system(cmd)
            f_sra = "%s.sra" % rid
            assert op.isfile(f_sra), "%s is not there" % f_sra
            print >>fhc, "fastq-dump --disable-multithreading --gzip --split-files -outdir %s %s/%s.sra" % (d05, d03, rid)
        fhc.close()
        os.chdir("%s/pbs" % os.environ['code'])
        jname = name + "Job"
        assert op.isfile(name), "no %s in pbs" % name
        os.system("qsub %s -v JOB=%s,PID=%s" % (name, jname, pid))
        os.system("touch %s/cps/%s" % (dirw, name))
    def output(self):
        dirc = "%s/rnaseq/%s/cps" % (os.environ['misc2'], self.pid)
        return luigi.LocalTarget("%s/%s" % (dirc, self.name))
class rnaseq1FastQC(luigi.Task):
    pid = luigi.Parameter()
    name = luigi.Parameter(default="rnaseq1FastQC")
    def requires(self):
        dirw = "%s/rnaseq/%s" % (os.environ['misc2'], self.pid)
        dirc = "%s/cps"
        f11 = "%s/11.tsv" % dirw
        if op.isfile(f11) and os.stat(f11).st_size > 0:
            return None
        else:
            return [rnaseq0Sra(self.pid), WaitJob("%s/rnaseq0SraJob" % dirc)]
    def run(self):
        pid, name = self.pid, self.name
        dirw = op.join(os.environ['misc2'], 'rnaseq', pid)
        if not op.isdir(dirw): os.makedirs(dirw)
        os.chdir(dirw)
        if not op.isdir("cps"): os.makedirs("cps")
        assert op.isfile("11.tsv"), "no 11.tsv in %s" % dirw
        ary = np.genfromtxt("11.tsv", names = True, dtype = None, delimiter = "\t")
        if not op.isdir("13.fastqc"): os.makedirs("13.fastqc")
        fho = open("12.fastqc.sh", "w")
        for row in ary:
            rid, dirf, read1, read2 = row
            f1 = "%s/%s" % (dirf, read1)
            f2 = "%s/%s" % (dirf, read2)
            assert op.isfile(f1), "%s not there" % f1
            assert op.isfile(f2), "%s not there" % f2
            print >>fho, "fastqc -o 13.fastqc --noextract -f fastq %s" % f1
            print >>fho, "fastqc -o 13.fastqc --noextract -f fastq %s" % f2
        fho.close()
        os.chdir("%s/pbs" % os.environ['code'])
        jname = name + "Job"
        assert op.isfile(name), "no %s in pbs" % name
        os.system("qsub %s -v JOB=%s,PID=%s" % (name, jname, pid))
        os.system("touch %s/cps/%s" % (dirw, name))
    def output(self):
        dirc = "%s/rnaseq/%s/cps" % (os.environ['misc2'], self.pid)
        return luigi.LocalTarget("%s/%s" % (dirc, self.name))
class rnaseq2Trim(luigi.Task):
    pid = luigi.Parameter()
    name = luigi.Parameter(default="rnaseq2Trim")
    def requires(self):
        dirc = "%s/rnaseq/%s/cps" % (os.environ['misc2'], self.pid)
        return [rnaseq1FastQC(self.pid), WaitJob("%s/rnaseq1FastQCJob" % dirc)]
    def run(self):
        pid, name = self.pid, self.name
        dirw = op.join(os.environ['misc2'], 'rnaseq', pid)
        os.chdir(dirw)
        assert op.isfile("11.tsv"), "no 11.tsv in %s" % dirw
        ary = np.genfromtxt("11.tsv", names = True, dtype = None, delimiter = "\t")
        trimm = "%s/git/trimmomatic" % os.environ["src"]
        assert op.isdir(trimm), "%s is not there" % trimm
        if not op.isdir("15.trimmomatic"): os.makedirs("15.trimmomatic")
        fho = open("15.trimmomatic.cmd", "w")
        for row in ary:
            rid, dirf, read1, read2 = row
            f1 = "%s/%s" % (dirf, read1)
            f2 = "%s/%s" % (dirf, read2)
            pre1 = "15.trimmomatic/%s" % op.splitext(read1)[0]
            pre2 = "15.trimmomatic/%s" % op.splitext(read2)[0]
            print >>fho, "java -jar %s/trimmomatic.jar PE -threads 2 %s %s %s.PE.fq.gz %s.SE.fq.gz %s.PE.fq.gz %s.SE.fq.gz ILLUMINACLIP:%s/adapters/TruSeq2-PE.fa:2:30:10:8:no LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36" % (trimm, f1, f2, pre1, pre1, pre2, pre2, trimm)
        os.chdir("%s/pbs" % os.environ['code'])
        jname = name + "Job"
        assert op.isfile(name), "no %s in pbs" % name
        #os.system("qsub %s -v JOB=%s,PID=%s" % (name, jname, pid))
        #os.system("touch %s/cps/%s" % (dirw, name))
    def output(self):
        dirc = "%s/rnaseq/%s/cps" % (os.environ['misc2'], self.pid)
        return luigi.LocalTarget("%s/%s" % (dirc, self.name))

if __name__ == "__main__":
    luigi.run()
