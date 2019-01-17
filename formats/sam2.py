#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
SAM alignment format. There are other tools that handles better SAM and BAM.
This script simply parses the lines in SAM into human readable fields.

http://samtools.sourceforge.net/SAM1.pdf
"""

import os
import os.path as op
import sys
import logging

from collections import defaultdict
from itertools import groupby

from maize.formats.base import LineFile, must_open
from maize.formats.fasta import Fasta
from maize.formats.sizes import Sizes
from maize.utils.cbook import fill
from maize.apps.base import need_update, sh, mkdir, glob, get_abs_path

class SamLine (object):

    def __init__(self, row):

        args = row.strip().split("\t")
        self.qname = args[0]
        self.flag = int(args[1])
        self.rname = args[2]
        self.pos = args[3]
        self.mapq = args[4]
        self.cigar = args[5]
        self.mrnm = args[6]
        self.mpos = args[7]
        self.isize = args[8]
        self.seq = args[9]
        self.qual = args[10]
        self.extra = args[11:]

    def __str__(self):
        return  "\t".join(str(x) for x in (self.qname, self.flag,
                        self.rname, self.pos, self.mapq,
                        self.cigar, self.mrnm, self.mpos,
                        self.isize, self.seq, self.qual,
                        "\t".join(self.extra)))

    @property
    def orientation(self):
        return '-' if self.flag & 0x10 == 0 else '+'

    def update_readname(self):
        if self.flag & 0x40 == 0:
            tag = "/1"
        elif self.flag & 0x80 == 0:
            tag = "/2"
        else:
            tag = ""
        self.qname += tag

    @property
    def pairline(self):
        qpos = self.cigar.split('H', 1)[0]
        return "%s:%s\t%s:%s" % (self.qname, qpos, self.rname, self.pos)

class Sam (LineFile):

    def __init__(self, filename, callback=None):

        fp = open(filename)
        for row in fp:
            if row[0] == '@':
                continue
            s = SamLine(row)
            if callback:
                callback(s)

def output_bam(cmd, outfile, cpus=8):
    bam = outfile.endswith(".bam")
    if not bam:
        return cmd + " > {0}".format(outfile)

    outcmd, mflag = ("samtools view -bS", "-@ {0}".format(cpus))
    cmd += " | {0} {1} - > {2}".format(outcmd, mflag, outfile)

    return cmd

class GenomeCoverageLine (object):

    def __init__(self, row):
        args = row.split()
        self.seqid = args[0]
        self.depth = int(args[1])
        self.positions = int(args[2])
        self.length = int(args[3])
        self.freq = float(args[4])

class GenomeCoverageFile (LineFile):

    def __init__(self, filename):
        super(GenomeCoverageFile, self).__init__(filename)
        fp = open(filename)
        for row in fp:
            self.append(GenomeCoverageLine(row))

    def iter_coverage_seqid(self):
        for seqid, lines in groupby(self, key=lambda x: x.seqid):
            lines = list(lines)
            length = lines[0].length
            counts = 0
            for r in lines:
                counts += r.depth * r.positions
            yield seqid, counts * 1. / length

def get_prefix(readfile, dbfile):
    rdpf = op.basename(readfile).replace(".gz", "").rsplit(".", 1)[0]
    dbpf = op.basename(dbfile).split(".")[0]
    return ".".join((rdpf, dbpf))

def get_samfile(readfile, dbfile, bam=False, mapped=False,
                unmapped=False, bowtie=False):
    prefix = get_prefix(readfile, dbfile)
    ext = ".bam" if bam else ".sam"
    samfile = prefix + ext
    ext = ".fastq" if bowtie else ext
    mapped = (prefix + ".mapped" + ext) if mapped else None
    unmapped = (prefix + ".unmapped" + ext) if unmapped else None
    return samfile, mapped, unmapped

def get_minibam(bamfile, region, overwrite=True):
    xregion = region.replace(":", "_").replace("-", "_").replace(",", "")
    minibamfile = op.basename(bamfile).replace(".bam", ".{}.bam".format(xregion))
    baifile = minibamfile + ".bai"
    if op.exists(baifile):
        sh("rm {}".format(baifile))

    if not overwrite and op.exists(minibamfile):
        logging.error("Output name exists: `{}`".format(minibamfile))
        return

    cmd = "samtools view {} {} -b".format(bamfile, region)
    cmd += " -o {0}".format(minibamfile)

    sh(cmd)
    sh("samtools index {0}".format(minibamfile))

    return minibamfile

def get_minibam_bed(bamfile, bedfile, minibam=None):
    """ samtools view -L could do the work, but it is NOT random access. Here we
    are processing multiple regions sequentially. See also:

    https://www.biostars.org/p/49306/
    """
    pf = op.basename(bedfile).split(".")[0]
    minibamfile = minibam or op.basename(bamfile).replace(".bam", ".{}.bam".format(pf))
    minisamfile = minibam.replace(".bam", ".sam")
    baifile = minibamfile + ".bai"
    if op.exists(baifile):
        sh("rm {}".format(baifile))

    cmd = "samtools view -H {} > {}".format(bamfile, minisamfile)
    sh(cmd)

    cmd = "cat {}".format(bedfile)
    cmd += " | perl -lane 'print \"$F[0]:$F[1]-$F[2]\"'"
    cmd += " | xargs -n1 -t -I \{\}"
    cmd += " samtools view {}".format(bamfile)
    cmd += " \{\} >> " + minisamfile
    sh(cmd)

    cmd = "samtools view {} -b".format(minisamfile)
    cmd += " | samtools sort -"
    cmd += " -o {0}".format(minibamfile)

    sh(cmd)
    sh("samtools index {0}".format(minibamfile))
    return minibamfile

def main():
    import argparse
    parser = argparse.ArgumentParser(
            formatter_class = argparse.ArgumentDefaultsHelpFormatter,
            description = 'SAM utilities'
    )
    sp = parser.add_subparsers(title = 'available commands', dest = 'command')

    # Alter read names
    sp1 = sp.add_parser("append", help = 'append or prepend string to read names',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.add_argument('bam', help = 'sam/bam file')
    sp1.add_argument("--prepend", help="Prepend string to read names")
    sp1.set_defaults(func = append)
 
    # Extract info
    sp1 = sp.add_parser('bed', help='convert bam files to bed',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = bed)
    sp1 = sp.add_parser('fastq', help='convert bam files to paired fastq',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = fastq)
    sp1 = sp.add_parser('pair', help='parse sam file and get pairs',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = pair)
    sp1 = sp.add_parser('pairs', help='print paired-end reads from BAM file',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = pairs)
    sp1 = sp.add_parser('chimera', help='parse sam file from `bwasw` and list multi-hit reads',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = chimera)
    sp1 = sp.add_parser('noclip', help='remove clipped reads from bam',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = noclip)
    sp1 = sp.add_parser('ace', help='convert sam file to ace',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = ace)
    sp1 = sp.add_parser('consensus', help='convert bam alignments to consensus FASTA',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = consensus)
    sp1 = sp.add_parser('fpkm', help='calculate FPKM values from BAM file',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = fpkm)
    sp1 = sp.add_parser('coverage', help='calculate depth for BAM file',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = coverage)
    sp1 = sp.add_parser('vcf', help='call SNPs on a set of bam files',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = vcf)
    sp1 = sp.add_parser('mapped', help='extract mapped/unmapped reads from samfile',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = mapped)
    sp1 = sp.add_parser('count', help='count the number of reads mapped using htseq',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = count)
    sp1 = sp.add_parser('merge', help='merge bam files',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = merge)
    # Convenience function
    sp1 = sp.add_parser('index', help='convert to bam, sort and then index',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = index)
    sp1 = sp.add_parser('mini', help='extract mini-bam for a single region',
            formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    sp1.set_defaults(func = mini)
    
    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        print('Error: need to specify a sub command\n')
        parser.print_help()

def fastq(args):
    """
    %prog fastq bamfile prefix

    Convert BAM files to paired FASTQ files.
    """
    p = OptionParser(fastq.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    bamfile, pf = args
    singletons = pf + ".se.fastq"
    a = pf + ".read1.fastq"
    b = pf + ".read2.fastq"

    cmd  = "samtools collate -uOn 128 {} tmp-prefix".format(bamfile)
    cmd += " | samtools fastq -s {} -1 {} -2 {} -"\
                .format(singletons, a, b)
    sh(cmd)

    if os.stat(singletons).st_size == 0:  # singleton file is empty
        os.remove(singletons)
    return a, b

def mini(args):
    """
    %prog mini bamfile region

    Extract mini-bam for a single region.
    """
    p = OptionParser(mini.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    bamfile, region = args
    get_minibam(bamfile, region)

def noclip(args):
    """
    %prog noclip bamfile

    Remove clipped reads from BAM.
    """
    p = OptionParser(noclip.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    bamfile, = args
    noclipbam = bamfile.replace(".bam", ".noclip.bam")
    cmd = "samtools view -h {} | awk -F '\t' '($6 !~ /H|S/)'".format(bamfile)
    cmd += " | samtools view -@ 4 -b -o {}".format(noclipbam)
    sh(cmd)

    sh("samtools index {}".format(noclipbam))

def append(args):
    """
    %prog append bamfile

    Append /1 or /2 to read names. Useful for using the Tophat2 bam file for
    training AUGUSTUS gene models.
    """
    bamfile = args.bam
    prepend = args.prepend

    icmd = "samtools view -h {0}".format(bamfile)
    bamfile = bamfile.rsplit(".", 1)[0] + ".append.bam"
    ocmd = "samtools view -b -@ 64 - -o {0}".format(bamfile)
    p = Popen(ocmd, stdin=PIPE)
    for row in popen(icmd):
        if row[0] == '@':
            print >> p.stdin, row.strip()
        else:
            s = SamLine(row)
            if prepend:
                s.qname = prepend + "_" + s.qname
            else:
                s.update_readname()
            print >> p.stdin, s

def bed(args):
    """
    %prog bed bedfile bamfiles

    Convert bam files to bed.
    """
    p = OptionParser(bed.__doc__)
    opts, args = p.parse_args(args)

    if len(args) < 2:
        sys.exit(not p.print_help())

    bedfile = args[0]
    bamfiles = args[1:]
    for bamfile in bamfiles:
        cmd = "bamToBed -i {0}".format(bamfile)
        sh(cmd, outfile=bedfile, append=True)

def merge(args):
    """
    %prog merge merged_bams bams1_dir bams2_dir ...

    Merge BAM files. Treat the bams with the same prefix as a set.
    Output the commands first.
    """
    from maize.apps.grid import MakeManager

    p = OptionParser(merge.__doc__)
    p.set_sep(sep="_", help="Separator to group per prefix")
    opts, args = p.parse_args(args)

    if len(args) < 2:
        sys.exit(not p.print_help())

    merged_bams = args[0]
    bamdirs = args[1:]

    mkdir(merged_bams)
    bams = []
    for x in bamdirs:
        bams += glob(op.join(x, "*.bam"))
    bams = [x for x in bams if "nsorted" not in x]

    logging.debug("Found a total of {0} BAM files.".format(len(bams)))

    sep = args.sep
    key = lambda x: op.basename(x).split(sep)[0]
    bams.sort(key=key)
    mm = MakeManager()
    for prefix, files in groupby(bams, key=key):
        files = sorted(list(files))
        nfiles = len(files)
        source = " ".join(files)
        target = op.join(merged_bams, op.basename(files[0]))
        if nfiles == 1:
            source = get_abs_path(source)
            cmd = "ln -s {0} {1}".format(source, target)
            mm.add("", target, cmd)
        else:
            cmd = "samtools merge -@ 8 {0} {1}".format(target, source)
            mm.add(files, target, cmd, remove=True)
    mm.write()

def count(args):
    """
    %prog count bamfile gtf

    Count the number of reads mapped using `htseq-count`.
    """
    p = OptionParser(count.__doc__)
    sp1.add_argument("--type", default="exon",
                 help="Only count feature type")
    p.set_cpus(cpus=8)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    bamfile, gtf = args
    cpus = args.cpus
    pf = bamfile.split(".")[0]
    countfile = pf + ".count"
    if not need_update(bamfile, countfile):
        return

    nsorted = pf + "_nsorted"
    nsortedbam, nsortedsam = nsorted + ".bam", nsorted + ".sam"
    if need_update(bamfile, nsortedsam):
        cmd = "samtools sort -@ {0} -n {1} {2}".format(cpus, bamfile, nsorted)
        sh(cmd)
        cmd = "samtools view -@ {0} -h {1}".format(cpus, nsortedbam)
        sh(cmd, outfile=nsortedsam)

    if need_update(nsortedsam, countfile):
        cmd = "htseq-count --stranded=no --minaqual=10"
        cmd += " -t {0}".format(args.type)
        cmd += " {0} {1}".format(nsortedsam, gtf)
        sh(cmd, outfile=countfile)

def coverage(args):
    """
    %prog coverage fastafile bamfile

    Calculate coverage for BAM file. BAM file will be sorted unless with
    --nosort.
    """
    p = OptionParser(coverage.__doc__)
    sp1.add_argument("--format", default="bigwig",
                 choices=("bedgraph", "bigwig", "coverage"),
                 help="Output format")
    sp1.add_argument("--nosort", default=False, action="store_true",
                 help="Do not sort BAM")
    p.set_outfile()
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    fastafile, bamfile = args
    format = args.format
    if args.nosort:
        logging.debug("BAM sorting skipped")
    else:
        bamfile = index([bamfile, "--fasta={0}".format(fastafile)])

    pf = bamfile.rsplit(".", 2)[0]
    sizesfile = Sizes(fastafile).filename
    cmd = "genomeCoverageBed -ibam {0} -g {1}".format(bamfile, sizesfile)
    if format in ("bedgraph", "bigwig"):
        cmd += " -bg"
        bedgraphfile = pf + ".bedgraph"
        sh(cmd, outfile=bedgraphfile)

        if format == "bedgraph":
            return bedgraphfile

        bigwigfile = pf + ".bigwig"
        cmd = "bedGraphToBigWig {0} {1} {2}".\
                    format(bedgraphfile, sizesfile, bigwigfile)
        sh(cmd)
        return bigwigfile

    coveragefile = pf + ".coverage"
    if need_update(fastafile, coveragefile):
        sh(cmd, outfile=coveragefile)

    gcf = GenomeCoverageFile(coveragefile)
    fw = must_open(args.outfile, "w")
    for seqid, cov in gcf.iter_coverage_seqid():
        print >> fw, "\t".join((seqid, "{0:.1f}".format(cov)))
    fw.close()

def fpkm(args):
    """
    %prog fpkm fastafile *.bam

    Calculate FPKM values from BAM file.
    """
    p = OptionParser(fpkm.__doc__)
    opts, args = p.parse_args(args)

    if len(args) < 2:
        sys.exit(not p.print_help())

    fastafile = args[0]
    bamfiles = args[1:]
    # Create a DUMMY gff file for cuffdiff
    gffile = fastafile.rsplit(".", 1)[0] + ".gff"
    if need_update(fastafile, gffile):
        fw = open(gffile, "w")
        f = Fasta(fastafile, lazy=True)
        for key, size in f.itersizes_ordered():
            print >> fw, "\t".join(str(x) for x in (key, "dummy", "transcript",\
                1, size, ".", ".", ".", "ID=" + key))
        fw.close()
        logging.debug("Dummy GFF created: {0}".format(gffile))

    cmd = "cuffdiff {0} {1}".format(gffile, " ".join(bamfiles))
    sh(cmd)

def pairs(args):
    """
    See __doc__ for OptionParser.set_pairs().
    """
    import maize.formats.bed

    p = OptionParser(pairs.__doc__)
    p.set_pairs()
    opts, targs = p.parse_args(args)

    if len(targs) != 1:
        sys.exit(not p.print_help())

    samfile, = targs
    bedfile = samfile.rsplit(".", 1)[0] + ".bed"
    if need_update(samfile, bedfile):
        cmd = "bamToBed -i {0}".format(samfile)
        sh(cmd, outfile=bedfile)

    args[args.index(samfile)] = bedfile

    return maize.formats.bed.pairs(args)

def consensus(args):
    """
    %prog consensus fastafile bamfile

    Convert bam alignments to consensus FASTQ/FASTA.
    """
    p = OptionParser(consensus.__doc__)
    sp1.add_argument("--fasta", default=False, action="store_true",
            help="Generate consensus FASTA sequences [default: %default]")
    sp1.add_argument("--mask", default=0, type="int",
            help="Mask bases with quality lower than")
    opts, args = p.parse_args(args)

    if len(args) < 2:
        sys.exit(not p.print_help())

    fastafile, bamfile = args
    fasta = args.fasta
    suffix = "fasta" if fasta else "fastq"
    pf = bamfile.rsplit(".", 1)[0]
    cnsfile = pf + ".cns.{0}".format(suffix)
    vcfgzfile = pf + ".vcf.gz"
    vcf([fastafile, bamfile, "-o", vcfgzfile])
    cmd += "zcat {0} | vcfutils.pl vcf2fq".format(vcfgzfile)
    if fasta:
        cmd += " | seqtk seq -q {0} -A -".format(args.mask)

    sh(cmd, outfile=cnsfile)

def vcf(args):
    """
    %prog vcf fastafile bamfiles > out.vcf.gz

    Call SNPs on bam files.
    """
    from maize.apps.grid import Jobs

    valid_callers = ("mpileup", "freebayes")
    p = OptionParser(vcf.__doc__)
    p.set_outfile(outfile="out.vcf.gz")
    sp1.add_argument("--nosort", default=False, action="store_true",
                 help="Do not sort the BAM files")
    sp1.add_argument("--caller", default="mpileup", choices=valid_callers,
                 help="Use variant caller [default: %default]")
    opts, args = p.parse_args(args)

    if len(args) < 2:
        sys.exit(not p.print_help())

    fastafile = args[0]
    bamfiles = args[1:]
    caller = args.caller

    unsorted = [x for x in bamfiles if ".sorted." not in x]
    if args.nosort:
        bamfiles = unsorted
    else:
        jargs = [[[x, "--unique"]] for x in unsorted]
        jobs = Jobs(index, args=jargs)
        jobs.run()
        bamfiles = [x.replace(".sorted.bam", ".bam") for x in bamfiles]
        bamfiles = [x.replace(".bam", ".sorted.bam") for x in bamfiles]

    if caller == "mpileup":
        cmd = "samtools mpileup -E -uf"
        cmd += " {0} {1}".format(fastafile, " ".join(bamfiles))
        cmd += " | bcftools call -vmO v"
    elif caller == "freebayes":
        cmd = "freebayes -f"
        cmd += " {0} {1}".format(fastafile, " ".join(bamfiles))
    sh(cmd, outfile=args.outfile)

def breakpoint(r):
    op_prev = None
    cum_length = 0
    is_clip = lambda x: x in (4, 5)
    rl = sum(l for o, l in r.cigartuples)
    for op, length in r.cigartuples:
        if is_clip(op) != is_clip(op_prev) and op_prev is not None:
            yield rl - cum_length if r.is_reverse else cum_length
        op_prev = op
        cum_length += length

def chimera(args):
    """
    %prog chimera bamfile

    Parse BAM file from `bwasw` and list multi-hit reads and breakpoints.
    """
    import pysam
    from maize.utils.natsort import natsorted

    p = OptionParser(chimera.__doc__)
    p.set_verbose()
    opts, args = p.parse_args(args)
    if len(args) != 1:
        sys.exit(not p.print_help())

    samfile, = args
    samfile = pysam.AlignmentFile(samfile)
    rstore = defaultdict(list)
    hstore = defaultdict(int)
    for r in samfile.fetch():
        rstore[r.query_name] += list(breakpoint(r))
        hstore[r.query_name] += 1
        if args.verbose:
            logging.error(r.query_name, "+-"[r.is_reverse], \
                        sum(l for o, l in r.cigartuples), r.cigarstring, list(breakpoint(r)))

    for rn, bps in natsorted(rstore.items()):
        bps = "|".join(str(x) for x in sorted(bps)) if bps else "na"
        print("\t".join((rn, str(hstore[rn]), bps)))

def index(args):
    """
    %prog index samfile/bamfile

    If SAM file, convert to BAM, sort and then index, using SAMTOOLS
    """
    p = OptionParser(index.__doc__)
    sp1.add_argument("--fasta", dest="fasta", default=None,
            help="add @SQ header to the BAM file [default: %default]")
    sp1.add_argument("--unique", default=False, action="store_true",
            help="only retain uniquely mapped reads [default: %default]")
    p.set_cpus()
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(p.print_help())

    samfile, = args
    cpus = args.cpus
    fastafile = args.fasta
    if fastafile:
        assert op.exists(fastafile)

    bamfile = samfile.replace(".sam", ".bam")
    if fastafile:
        faifile = fastafile + ".fai"
        if need_update(fastafile, faifile):
            sh("samtools faidx {0}".format(fastafile))
        cmd = "samtools view -bt {0} {1} -o {2}".\
                format(faifile, samfile, bamfile)
    else:
        cmd = "samtools view -bS {0} -o {1}".\
                format(samfile, bamfile)

    cmd += " -@ {0}".format(cpus)
    if args.unique:
        cmd += " -q 1"

    if samfile.endswith(".sam") and need_update(samfile, bamfile):
        sh(cmd)

    # Already sorted?
    if bamfile.endswith(".sorted.bam"):
        sortedbamfile = bamfile
    else:
        prefix = bamfile.replace(".bam", "")
        sortedbamfile = prefix + ".sorted.bam"

    if need_update(bamfile, sortedbamfile):
        cmd = "samtools sort {0} -o {1}".format(bamfile, sortedbamfile)
        cmd += " -@ {0}".format(cpus)
        sh(cmd)

    baifile = sortedbamfile + ".bai"
    if need_update(sortedbamfile, baifile):
        sh("samtools index {0}".format(sortedbamfile))

    return sortedbamfile

def mapped(args):
    """
    %prog mapped sam/bamfile

    Given an input sam/bam file, output a sam/bam file containing only the mapped reads.
    Optionally, extract the unmapped reads into a separate file
    """
    import pysam
    from maize.apps.grid import Jobs

    p = OptionParser(mapped.__doc__)
    p.set_sam_options(extra=False)

    opts, args = p.parse_args(args)
    if len(args) != 1:
        sys.exit(p.print_help())

    samfile, = args

    view_opts = []
    oext, mopts = (".sam", ["-S"]) \
            if samfile.endswith(".sam") else (".bam", [])

    flag, ext = ("-b", ".bam") if args.bam else ("-h", ".sam")
    margs.append(flag)

    if args.uniq:
        margs.append("-q1")
        ext = ".uniq{0}".format(ext)

    if args.unmapped:
        uopts = [x for x in mopts]
        uoutfile = samfile.replace(oext, ".unmapped{0}".format(ext))
        uargs.extend(["-f4", samfile, "-o{0}".format(uoutfile)])
        view_args.append(uopts)

    outfile = samfile.replace(oext, ".mapped{0}".format(ext))
    margs.extend(["-F4", samfile, "-o{0}".format(outfile)])
    view_args.append(mopts)

    for vo in view_opts:
        logging.debug('samtools view {0}'.format(" ".join(vo)))

    jobs = Jobs(pysam.view, [(z for z in x) for x in view_opts])
    jobs.run()

def pair(args):
    """
    %prog pair samfile

    Parses the sam file and retrieve in pairs format,
    query:pos ref:pos
    """
    p = OptionParser(pair.__doc__)

    opts, args = p.parse_args(args)
    if len(args) != 1:
        sys.exit(p.print_help())

    def callback(s):
        print(s.pairline)
    Sam(args[0], callback=callback)

def cigar_to_seq(a, gap='*'):
    """
    Accepts a pysam row.

    cigar alignment is presented as a list of tuples (operation,length). For
    example, the tuple [ (0,3), (1,5), (0,2) ] refers to an alignment with 3
    matches, 5 insertions and another 2 matches.

    Op BAM Description
    M 0 alignment match (can be a sequence match or mismatch)
    I 1 insertion to the reference
    D 2 deletion from the reference
    N 3 skipped region from the reference
    S 4 soft clipping (clipped sequences present in SEQ)
    H 5 hard clipping (clipped sequences NOT present in SEQ)
    P 6 padding (silent deletion from padded reference)
    = 7 sequence match
    X 8 sequence mismatch

    convert the sequence based on the cigar string. For example:
    """
    seq, cigar = a.seq, a.cigar
    start = 0
    subseqs = []
    npadded = 0
    if cigar is None:
        return None, npadded

    for operation, length in cigar:
        end = start if operation == 2 else start + length

        if operation == 0:  # match
            subseq = seq[start:end]
        elif operation == 1:  # insertion
            subseq = ""
        elif operation == 2:  # deletion
            subseq = gap * length
            npadded += length
        elif operation == 3:  # skipped
            subseq = 'N' * length
        elif operation in (4, 5):  # clip
            subseq = ""
        else:
            raise NotImplementedError

        subseqs.append(subseq)
        start = end

    return "".join(subseqs), npadded

def ace(args):
    """
    %prog ace bamfile fastafile

    convert bam format to ace format. This often allows the remapping to be
    assessed as a denovo assembly format. bam file needs to be indexed. also
    creates a .mates file to be used in amos/bambus, and .astat file to mark
    whether the contig is unique or repetitive based on A-statistics in Celera
    assembler.
    """
    p = OptionParser(ace.__doc__)
    sp1.add_argument("--splitdir", dest="splitdir", default="outRoot",
            help="split the ace per contig to dir [default: %default]")
    sp1.add_argument("--unpaired", dest="unpaired", default=False,
            help="remove read pairs on the same contig [default: %default]")
    sp1.add_argument("--minreadno", dest="minreadno", default=3, type="int",
            help="minimum read numbers per contig [default: %default]")
    sp1.add_argument("--minctgsize", dest="minctgsize", default=100, type="int",
            help="minimum contig size per contig [default: %default]")
    sp1.add_argument("--astat", default=False, action="store_true",
            help="create .astat to list repetitiveness [default: %default]")
    sp1.add_argument("--readids", default=False, action="store_true",
            help="create file of mapped and unmapped ids [default: %default]")

    from pysam import Samfile

    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    bamfile, fastafile = args
    astat = args.astat
    readids = args.readids

    f = Fasta(fastafile)
    prefix = bamfile.split(".")[0]
    acefile = prefix + ".ace"
    readsfile = prefix + ".reads"
    astatfile = prefix + ".astat"

    logging.debug("Load {0}".format(bamfile))
    s = Samfile(bamfile, "rb")

    ncontigs = s.nreferences
    genomesize = sum(x for a, x in f.itersizes())
    logging.debug("Total {0} contigs with size {1} base".format(ncontigs,
        genomesize))
    qual = "20"  # default qual

    totalreads = sum(s.count(x) for x in s.references)
    logging.debug("Total {0} reads mapped".format(totalreads))

    fw = open(acefile, "w")
    if astat:
        astatfw = open(astatfile, "w")
    if readids:
        readsfw = open(readsfile, "w")

    print >> fw, "AS {0} {1}".format(ncontigs, totalreads)
    print >> fw

    for i, contig in enumerate(s.references):
        cseq = f[contig]
        nbases = len(cseq)

        mapped_reads = [x for x in s.fetch(contig) if not x.is_unmapped]
        nreads = len(mapped_reads)

        nsegments = 0
        print >> fw, "CO {0} {1} {2} {3} U".format(contig, nbases, nreads,
                nsegments)
        print >> fw, fill(str(cseq.seq))
        print >> fw

        if astat:
            astat = Astat(nbases, nreads, genomesize, totalreads)
            print >> astatfw, "{0}\t{1:.1f}".format(contig, astat)

        text = fill([qual] * nbases, delimiter=" ", width=30)
        print >> fw, "BQ\n{0}".format(text)
        print >> fw

        rnames = []
        for a in mapped_reads:
            readname = a.qname
            rname = readname

            if readids:
                print >> readsfw, readname
            rnames.append(rname)

            strand = "C" if a.is_reverse else "U"
            paddedstart = a.pos + 1  # 0-based to 1-based
            af = "AF {0} {1} {2}".format(rname, strand, paddedstart)
            print >> fw, af

        print >> fw

        for a, rname in zip(mapped_reads, rnames):
            aseq, npadded = cigar_to_seq(a)
            if aseq is None:
                continue

            ninfos = 0
            ntags = 0
            alen = len(aseq)
            rd = "RD {0} {1} {2} {3}\n{4}".format(rname, alen, ninfos, ntags,
                    fill(aseq))
            qs = "QA 1 {0} 1 {0}".format(alen)

            print >> fw, rd
            print >> fw
            print >> fw, qs
            print >> fw

if __name__ == '__main__':
    main()
