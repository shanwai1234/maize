#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import os
import os.path as op
import sys
import logging

import numpy as np

from maize.formats.base import LineFile
from maize.apps.base import need_update, sh, get_abs_path, which

class Sizes (LineFile):
    """
    Two-column .sizes file, often generated by `faSize -detailed`
    contigID size
    """
    def __init__(self, filename, select=None):
        assert op.exists(filename), "File `{0}` not found".format(filename)

        # filename can be both .sizes file or FASTA formatted file
        sizesname = filename

        if not filename.endswith(".sizes"):
            sizesname = filename + ".sizes"
            filename = get_abs_path(filename)
            if need_update(filename, sizesname):
                cmd = "faSize"
                if which(cmd):
                    cmd += " -detailed {0}".format(filename)
                    sh(cmd, outfile=sizesname)
                else:
                    from jcvi.formats.fasta import Fasta

                    f = Fasta(filename)
                    fw = open(sizesname, "w")
                    for k, size in f.itersizes_ordered():
                        fw.write("\t".join((k, str(size))) + "\n")
                    fw.close()

            filename = sizesname

        assert filename.endswith(".sizes")

        super(Sizes, self).__init__(filename)
        self.fp = open(filename)
        self.filename = filename

        # get sizes for individual contigs, both in list and dict
        # this is to preserve the input order in the sizes file
        sizes = list(self.iter_sizes())
        if select:
            assert select > 0
            sizes = [x for x in sizes if x[1] >= select]
        self.sizes_mapping = dict(sizes)

        # get cumulative sizes, both in list and dict
        ctgs, sizes = zip(*sizes)
        self.sizes = sizes
        cumsizes = np.cumsum([0] + list(sizes))
        self.ctgs = ctgs
        self.cumsizes = cumsizes
        self.cumsizes_mapping = dict(zip(ctgs, cumsizes))

    def __len__(self):
        return len(self.sizes)

    def get_size(self, ctg):
        return self.sizes_mapping[ctg]

    def get_cumsize(self, ctg):
        return self.cumsizes_mapping[ctg]

    def close(self, clean=False):
        self.fp.close()
        if clean:
            os.remove(self.filename)

    @property
    def mapping(self):
        return self.sizes_mapping

    @property
    def totalsize(self):
        return sum(self.sizes)

    def iter_sizes(self):
        self.fp.seek(0)
        for row in self.fp:
            ctg, size = row.split()[:2]
            yield ctg, int(size)

    def iter_names(self):
        self.fp.seek(0)
        for row in self.fp:
            ctg, size = row.split()[:2]
            yield ctg

    def get_position(self, ctg, pos):
        if ctg not in self.cumsizes_mapping:
            return None
        return self.cumsizes_mapping[ctg] + pos

    def get_breaks(self):
        for i in xrange(len(self)):
            yield self.ctgs[i], self.cumsizes[i], self.cumsizes[i + 1]

    @property
    def summary(self):
        from jcvi.assembly.base import calculate_A50

        ctgsizes = self.sizes
        a50, l50, n50 = calculate_A50(ctgsizes)
        return sum(ctgsizes), l50, n50

def histogram(args):
    """
    %prog histogram [reads.fasta|reads.fastq]

    Plot read length distribution for reads. The plot would be similar to the
    one generated by SMRT-portal, for example:

    http://blog.pacificbiosciences.com/2013/10/data-release-long-read-shotgun.html

    Plot has two axes - corresponding to pdf and cdf, respectively.  Also adding
    number of reads, average/median, N50, and total length.
    """
    from jcvi.utils.cbook import human_size, thousands, SUFFIXES
    from jcvi.formats.fastq import fasta
    from jcvi.graphics.histogram import stem_leaf_plot
    from jcvi.graphics.base import plt, markup, human_formatter, \
                human_base_formatter, savefig, set2, set_ticklabels_helvetica

    p = OptionParser(histogram.__doc__)
    p.set_histogram(vmax=50000, bins=100, xlabel="Read length",
                    title="Read length distribution")
    p.add_option("--ylabel1", default="Counts",
                 help="Label of y-axis on the left")
    p.add_option("--color", default='0', choices=[str(x) for x in range(8)],
                 help="Color of bars, which is an index 0-7 in brewer set2")
    opts, args, iopts = p.set_image_options(args, figsize="6x6", style="dark")

    if len(args) != 1:
        sys.exit(not p.print_help())

    fastafile, = args
    fastafile, qualfile = fasta([fastafile, "--seqtk"])
    sizes = Sizes(fastafile)
    all_sizes = sorted(sizes.sizes)
    xmin, xmax, bins = opts.vmin, opts.vmax, opts.bins
    left, height = stem_leaf_plot(all_sizes, xmin, xmax, bins)

    plt.figure(1, (iopts.w, iopts.h))
    ax1 = plt.gca()

    width = (xmax - xmin) * .5 / bins
    color = set2[int(opts.color)]
    ax1.bar(left, height, width=width, linewidth=0, fc=color, align="center")
    ax1.set_xlabel(markup(opts.xlabel))
    ax1.set_ylabel(opts.ylabel1)

    ax2 = ax1.twinx()
    cur_size = 0
    total_size, l50, n50 = sizes.summary
    cdf = {}
    hsize = human_size(total_size)
    tag = hsize[-2:]
    unit = 1000 ** SUFFIXES[1000].index(tag)

    for x in all_sizes:
        if x not in cdf:
            cdf[x] = (total_size - cur_size) * 1. / unit
        cur_size += x
    x, y = zip(*sorted(cdf.items()))
    ax2.plot(x, y, '-', color="darkslategray")
    ylabel2 = "{0} above read length".format(tag)
    ax2.set_ylabel(ylabel2)

    for ax in (ax1, ax2):
        set_ticklabels_helvetica(ax)
        ax.set_xlim((xmin - width / 2, xmax + width / 2))

    tc = "gray"
    axt = ax1.transAxes
    xx, yy = .95, .95
    ma = "Total bases: {0}".format(hsize)
    mb = "Total reads: {0}".format(thousands(len(sizes)))
    mc = "Average read length: {0}bp".format(thousands(np.mean(all_sizes)))
    md = "Median read length: {0}bp".format(thousands(np.median(all_sizes)))
    me = "N50 read length: {0}bp".format(thousands(l50))
    for t in (ma, mb, mc, md, me):
        print >> sys.stderr, t
        ax1.text(xx, yy, t, color=tc, transform=axt, ha="right")
        yy -= .05

    ax1.set_title(markup(opts.title))
    # Seaborn removes ticks for all styles except 'ticks'. Now add them back:
    ax1.tick_params(axis="x", direction="out", length=3,
                    left=False, right=False, top=False, bottom=True)
    ax1.xaxis.set_major_formatter(human_base_formatter)
    ax1.yaxis.set_major_formatter(human_formatter)
    figname = sizes.filename + ".pdf"
    savefig(figname)

def extract(args):
    """
    %prog extract idsfile sizesfile

    Extract the lines containing only the given IDs.
    """
    p = OptionParser(extract.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    idsfile, sizesfile = args
    sizes = Sizes(sizesfile).mapping
    fp = open(idsfile)
    for row in fp:
        name = row.strip()
        size = sizes[name]
        print("\t".join(str(x) for x in (name, size)))

def agp(args):
    """
    %prog agp <fastafile|sizesfile>

    Convert the sizes file to a trivial AGP file.
    """
    from jcvi.formats.agp import OO

    p = OptionParser(agp.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    sizesfile, = args
    sizes = Sizes(sizesfile)
    agpfile = sizes.filename.rsplit(".", 1)[0] + ".agp"
    fw = open(agpfile, "w")
    o = OO()  # Without a filename
    for ctg, size in sizes.iter_sizes():
        o.add(ctg, ctg, size)

    o.write_AGP(fw)
    fw.close()
    logging.debug("AGP file written to `{0}`.".format(agpfile))

    return agpfile

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
            formatter_class = argparse.ArgumentDefaultsHelpFormatter,
            description = 'fasize utilities'
    )
    sp = parser.add_subparsers(title = 'available commands', dest = 'command')

    sp1 = sp.add_parser("histogram", help = "generate histogram for *.sizes")
    sp1.add_argument('fi', help = 'input *.sizes')
    sp1.set_defaults(func = histogram)
 
    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        print('Error: need to specify a sub command\n')
        parser.print_help()


