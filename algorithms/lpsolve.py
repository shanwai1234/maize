#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
Implement a few MIP solvers, based on benchmark found on <http://scip.zib.de/>
SCIP solver is ~16x faster than GLPK solver.  However, I found in rare cases
it will segfault. Therefore the default is SCIP, the program will switch to
GLPK solver for crashed cases.

The input lp_data is assumed in .lp format, see below

>>> lp_data = '''
... Maximize
...  5 x1 + 3 x2 + 2 x3
... Subject to
...  x2 + x3 <= 1
... Binary
...  x1
...  x2
...  x3
... End'''
>>> print SCIPSolver(lp_data).results
[0, 1]
>>> print GLPKSolver(lp_data).results
[0, 1]
"""

import os
import os.path as op
import shutil
import logging
#import cStringIO
import networkx as nx

from maize.utils.cbook import fill
from maize.utils.iter import pairwise
from maize.formats.base import flexible_cast
from maize.apps.base import sh, mkdir
from maize.algorithms.tsp import populate_edge_weights, node_to_edge

Work_dir = "lpsolve_work"

# CPLEX LP format
# <http://lpsolve.sourceforge.net/5.0/CPLEX-format.htm>
MAXIMIZE = "Maximize"
MINIMIZE = "Minimize"
SUBJECTTO = "Subject To"
BOUNDS = "Bounds"
BINARY = "Binary"
GENERNAL = "General"
END = "End"

class AbstractMIPSolver(object):
    """
    Base class for LP solvers
    """
    def __init__(self, lp_data, work_dir=Work_dir, clean=True, verbose=False):

        self.work_dir = work_dir
        self.clean = clean
        self.verbose = verbose

        mkdir(work_dir)

        lpfile = op.join(work_dir, "data.lp")  # problem instance
        logging.debug("write MIP instance to `{0}`".format(lpfile))

        fw = open(lpfile, "w")
        fw.write(lp_data)
        fw.close()

        retcode, outfile = self.run(lpfile)
        if retcode < 0:
            self.results = []
        else:
            self.results = self.parse_output(outfile)

        if self.results:
            logging.debug("optimized objective value ({0})".\
                    format(self.obj_val))

    def run(self, lp_data, work_dir):
        raise NotImplementedError

    def parse_output(self):
        raise NotImplementedError

    def cleanup(self):
        shutil.rmtree(self.work_dir)

class GLPKSolver(AbstractMIPSolver):
    """
    GNU Linear Programming Kit (GLPK) solver, wrapper for calling GLPSOL
    """
    def run(self, lpfile):

        outfile = op.join(self.work_dir, "data.lp.out")  # verbose output
        listfile = op.join(self.work_dir, "data.lp.list")  # simple output
        # cleanup in case something wrong happens
        for f in (outfile, listfile):
            if op.exists(f):
                os.remove(f)

        cmd = "glpsol --cuts --fpump --lp {0} -o {1} -w {2}".format(lpfile,
                outfile, listfile)

        outf = None if self.verbose else "/dev/null"
        retcode = sh(cmd, outfile=outf)

        if retcode == 127:
            logging.error("You need to install program `glpsol` " + \
                          "[http://www.gnu.org/software/glpk/]")
            return -1, None

        return retcode, listfile

    def parse_output(self, listfile, clean=False):

        fp = open(listfile)
        header = fp.readline()
        columns, rows = header.split()
        rows = int(rows)
        data = fp.readlines()
        self.obj_val = int(data[0].split()[-1])
        # the info are contained in the last several lines
        results = [int(x) for x in data[-rows:]]
        results = [i for i, x in enumerate(results) if x == 1]

        fp.close()

        if self.clean:
            self.cleanup()

        return results

class SCIPSolver(AbstractMIPSolver):
    """
    SCIP solver, wrapper for calling SCIP executable
    """
    def run(self, lpfile):

        outfile = self.work_dir + "/data.lp.out"  # verbose output
        if op.exists(outfile):
            os.remove(outfile)

        cmd = "scip -f {0} -l {1}".format(lpfile, outfile)

        outf = None if self.verbose else "/dev/null"
        retcode = sh(cmd, outfile=outf)

        if retcode == 127:
            logging.error("You need to install program `scip` " +\
                          "[http://scip.zib.de/]")
            return -1, None

        return retcode, outfile

    def parse_output(self, outfile):

        fp = open(outfile)
        for row in fp:
            if row.startswith("objective value"):
                obj_row = row
                break

        results = []
        for row in fp:
            """
            objective value:               8
            x1                             1   (obj:5)
            x2                             1   (obj:3)
            """
            if row.strip() == "":  # blank line ends the section
                break
            x = row.split()[0]
            results.append(int(x[1:]) - 1)  # 0-based indexing

        if results:
            self.obj_val = flexible_cast(obj_row.split(":")[1].strip())

        fp.close()

        if self.clean:
            self.cleanup()

        return results

class LPInstance (object):
    """
    CPLEX LP format commonly contains three blocks:
    objective, constraints, vars
    spec <http://lpsolve.sourceforge.net/5.0/CPLEX-format.htm>
    """
    def __init__(self):
        self.objective = MAXIMIZE
        self.sum = ""
        self.constraints = []
        self.bounds = []
        self.binaryvars = []
        self.generalvars = []

    def print_instance(self):
        self.handle = fw = cStringIO.StringIO()
        print >> fw, self.objective
        print >> fw, self.sum
        print >> fw, SUBJECTTO
        assert self.constraints, "Must contain constraints"
        print >> fw, "\n".join(self.constraints)
        if self.bounds:
            print >> fw, BOUNDS
            print >> fw, "\n".join(self.bounds)
        if self.binaryvars:
            print >> fw, BINARY
            print >> fw, "\n".join(self.binaryvars)
        if self.generalvars:
            print >> fw, GENERNAL
            print >> fw, "\n".join(self.generalvars)
        print >> fw, END

    def add_objective(self, edges, objective=MAXIMIZE):
        assert edges, "Edges must be non-empty"
        self.objective = objective
        items = [" + {0}x{1}".format(w, i + 1) \
                for i, (a, b, w) in enumerate(edges) if w]
        sums = fill(items, width=10)
        self.sum = sums

    def add_vars(self, nedges, offset=1, binary=True):
        vars = [" x{0}".format(i + offset) for i in xrange(nedges)]
        if binary:
            self.binaryvars = vars
        else:
            self.generalvars = vars

    def lpsolve(self, solver="scip", clean=True):
        self.print_instance()

        solver = SCIPSolver if solver == "scip" else GLPKSolver
        lp_data = self.handle.getvalue()
        self.handle.close()

        g = solver(lp_data, clean=clean)
        selected = set(g.results)
        try:
            obj_val = g.obj_val
        except AttributeError:  # No solution!
            return None, None
        return selected, obj_val

def summation(incident_edges):
    s = "".join(" + x{0}".format(i + 1) for i in incident_edges)
    return s

def edges_to_graph(edges):
    G = nx.DiGraph()
    for e in edges:
        a, b = e[:2]
        G.add_edge(a, b)
    return G

def edges_to_path(edges):
    """
    Connect edges and return a path.
    """
    if not edges:
        return None

    G = edges_to_graph(edges)
    path = nx.topological_sort(G)
    return path

def hamiltonian(edges, directed=False, constraint_generation=True):
    """
    Calculates shortest path that traverses each node exactly once. Convert
    Hamiltonian path problem to TSP by adding one dummy point that has a distance
    of zero to all your other points. Solve the TSP and get rid of the dummy
    point - what remains is the Hamiltonian Path.

    >>> g = [(1,2), (2,3), (3,4), (4,2), (3,5)]
    >>> hamiltonian(g)
    [1, 2, 4, 3, 5]
    >>> g = [(1,2), (2,3), (1,4), (2,5), (3,6)]
    >>> hamiltonian(g)
    """
    edges = populate_edge_weights(edges)
    incident, nodes = node_to_edge(edges, directed=False)
    if not directed:  # Make graph symmetric
        dual_edges = edges[:]
        for a, b, w in edges:
            dual_edges.append((b, a, w))
        edges = dual_edges

    DUMMY = "DUMMY"
    dummy_edges = edges + [(DUMMY, x, 0) for x in nodes] + \
                          [(x, DUMMY, 0) for x in nodes]

    #results = tsp(dummy_edges, constraint_generation=constraint_generation)
    results = tsp_gurobi(dummy_edges)
    if results:
        results = [x for x in results if DUMMY not in x]
        results = edges_to_path(results)
        if not directed:
            results = min(results, results[::-1])
    return results

def tsp_gurobi(edges):
    """
    Modeled using GUROBI python example.
    """
    from gurobipy import Model, GRB, quicksum

    edges = populate_edge_weights(edges)
    incoming, outgoing, nodes = node_to_edge(edges)
    idx = dict((n, i) for i, n in enumerate(nodes))
    nedges = len(edges)
    n = len(nodes)

    m = Model()

    step = lambda x: "u_{0}".format(x)
    # Create variables
    vars = {}
    for i, (a, b, w) in enumerate(edges):
        vars[i] = m.addVar(obj=w, vtype=GRB.BINARY, name=str(i))
    for u in nodes[1:]:
        u = step(u)
        vars[u] = m.addVar(obj=0, vtype=GRB.INTEGER, name=u)
    m.update()

    # Bounds for step variables
    for u in nodes[1:]:
        u = step(u)
        vars[u].lb = 1
        vars[u].ub = n - 1

    # Add degree constraint
    for v in nodes:
        incoming_edges = incoming[v]
        outgoing_edges = outgoing[v]
        m.addConstr(quicksum(vars[x] for x in incoming_edges) == 1)
        m.addConstr(quicksum(vars[x] for x in outgoing_edges) == 1)

    # Subtour elimination
    edge_store = dict(((idx[a], idx[b]), i) for i, (a, b, w) in enumerate(edges))

    # Given a list of edges, finds the shortest subtour
    def subtour(s_edges):
        visited = [False] * n
        cycles = []
        lengths = []
        selected = [[] for i in range(n)]
        for x, y in s_edges:
            selected[x].append(y)
        while True:
            current = visited.index(False)
            thiscycle = [current]
            while True:
                visited[current] = True
                neighbors = [x for x in selected[current] if not visited[x]]
                if len(neighbors) == 0:
                    break
                current = neighbors[0]
                thiscycle.append(current)
            cycles.append(thiscycle)
            lengths.append(len(thiscycle))
            if sum(lengths) == n:
                break
        return cycles[lengths.index(min(lengths))]

    def subtourelim(model, where):
        if where != GRB.callback.MIPSOL:
            return
        selected = []
        # make a list of edges selected in the solution
        sol = model.cbGetSolution([model._vars[i] for i in range(nedges)])
        selected = [edges[i] for i, x in enumerate(sol) if x > .5]
        selected = [(idx[a], idx[b]) for a, b, w in selected]
        # find the shortest cycle in the selected edge list
        tour = subtour(selected)
        if len(tour) == n:
            return
        # add a subtour elimination constraint
        c = tour
        incident = [edge_store[a, b] for a, b in pairwise(c + [c[0]])]
        model.cbLazy(quicksum(model._vars[x] for x in incident) <= len(tour) - 1)

    m.update()

    m._vars = vars
    m.params.LazyConstraints = 1
    m.optimize(subtourelim)

    selected = [v.varName for v in m.getVars() if v.x > .5]
    selected = [int(x) for x in selected if x[:2] != "u_"]
    results = sorted(x for i, x in enumerate(edges) if i in selected) \
                    if selected else None
    return results

def tsp(edges, constraint_generation=False):
    """
    Calculates shortest cycle that traverses each node exactly once. Also known
    as the Traveling Salesman Problem (TSP).
    """
    edges = populate_edge_weights(edges)
    incoming, outgoing, nodes = node_to_edge(edges)

    nedges, nnodes = len(edges), len(nodes)
    L = LPInstance()

    L.add_objective(edges, objective=MINIMIZE)
    balance = []
    # For each node, select exactly 1 incoming and 1 outgoing edge
    for v in nodes:
        incoming_edges = incoming[v]
        outgoing_edges = outgoing[v]
        icc = summation(incoming_edges)
        occ = summation(outgoing_edges)
        balance.append("{0} = 1".format(icc))
        balance.append("{0} = 1".format(occ))

    # Subtour elimination - Miller-Tucker-Zemlin (MTZ) formulation
    # <http://en.wikipedia.org/wiki/Travelling_salesman_problem>
    # Desrochers and laporte, 1991 (DFJ) has a stronger constraint
    # See also:
    # G. Laporte / The traveling salesman problem: Overview of algorithms
    start_step = nedges + 1
    u0 = nodes[0]
    nodes_to_steps = dict((n, start_step + i) for i, n in enumerate(nodes[1:]))
    edge_store = dict((e[:2], i) for i, e in enumerate(edges))
    mtz = []
    for i, e in enumerate(edges):
        a, b = e[:2]
        if u0 in (a, b):
            continue
        na, nb = nodes_to_steps[a], nodes_to_steps[b]
        con_ab = " x{0} - x{1} + {2}x{3}".format(na, nb, nnodes - 1, i + 1)
        if (b, a) in edge_store:  # This extra term is the stronger DFJ formulation
            j = edge_store[(b, a)]
            con_ab += " + {0}x{1}".format(nnodes - 3, j + 1)
        con_ab += " <= {0}".format(nnodes - 2)
        mtz.append(con_ab)

    # Step variables u_i bound between 1 and n, as additional variables
    bounds = []
    for i in xrange(start_step, nedges + nnodes):
        bounds.append(" 1 <= x{0} <= {1}".format(i, nnodes - 1))

    L.add_vars(nedges)

    """
    Constraint generation seek to find 'cuts' in the LP problem, by solving the
    relaxed form. The subtours were then incrementally added to the constraints.
    """
    if constraint_generation:
        L.constraints = balance
        subtours = []
        while True:
            selected, obj_val = L.lpsolve()
            results = sorted(x for i, x in enumerate(edges) if i in selected) \
                            if selected else None
            if not results:
                break
            G = edges_to_graph(results)
            cycles = list(nx.simple_cycles(G))
            if len(cycles) == 1:
                break
            for c in cycles:
                incident = [edge_store[a, b] for a, b in pairwise(c + [c[0]])]
                icc = summation(incident)
                subtours.append("{0} <= {1}".format(icc, len(incident) - 1))
            L.constraints = balance + subtours
    else:
        L.constraints = balance + mtz
        L.add_vars(nnodes - 1, offset=start_step, binary=False)
        L.bounds = bounds
        selected, obj_val = L.lpsolve()
        results = sorted(x for i, x in enumerate(edges) if i in selected) \
                        if selected else None

    return results

def path(edges, source, sink, flavor="longest"):
    """
    Calculates shortest/longest path from list of edges in a graph

    >>> g = [(1,2,1),(2,3,9),(2,4,3),(2,5,2),(3,6,8),(4,6,10),(4,7,4)]
    >>> g += [(6,8,7),(7,9,5),(8,9,6),(9,10,11)]
    >>> path(g, 1, 8, flavor="shortest")
    ([1, 2, 4, 6, 8], 21)
    >>> path(g, 1, 8, flavor="longest")
    ([1, 2, 3, 6, 8], 25)
    """
    outgoing, incoming, nodes = node_to_edge(edges)

    nedges = len(edges)
    L = LPInstance()

    assert flavor in ("longest", "shortest")

    objective = MAXIMIZE if flavor == "longest" else MINIMIZE
    L.add_objective(edges, objective=objective)

    # Balancing constraint, incoming edges equal to outgoing edges except
    # source and sink

    constraints = []
    for v in nodes:
        incoming_edges = incoming[v]
        outgoing_edges = outgoing[v]
        icc = summation(incoming_edges)
        occ = summation(outgoing_edges)

        if v == source:
            if not outgoing_edges:
                return None
            constraints.append("{0} = 1".format(occ))
        elif v == sink:
            if not incoming_edges:
                return None
            constraints.append("{0} = 1".format(icc))
        else:
            # Balancing
            constraints.append("{0}{1} = 0".format(icc, occ.replace('+', '-')))
            # Simple path
            if incoming_edges:
                constraints.append("{0} <= 1".format(icc))
            if outgoing_edges:
                constraints.append("{0} <= 1".format(occ))

    L.constraints = constraints
    L.add_vars(nedges)

    selected, obj_val = L.lpsolve()
    results = sorted(x for i, x in enumerate(edges) if i in selected) \
                    if selected else None
    results = edges_to_path(results)

    return results, obj_val

def min_feedback_arc_set(edges, remove=False, maxcycles=20000):
    """
    A directed graph may contain directed cycles, when such cycles are
    undesirable, we wish to eliminate them and obtain a directed acyclic graph
    (DAG). A feedback arc set has the property that it has at least one edge
    of every cycle in the graph. A minimum feedback arc set is the set that
    minimizes the total weight of the removed edges; or alternatively maximize
    the remaining edges. See: <http://en.wikipedia.org/wiki/Feedback_arc_set>.

    The MIP formulation proceeds as follows: use 0/1 indicator variable to
    select whether an edge is in the set, subject to constraint that each cycle
    must pick at least one such edge.

    >>> g = [(1, 2, 2), (2, 3, 2), (3, 4, 2)] + [(1, 3, 1), (3, 2, 1), (2, 4, 1)]
    >>> min_feedback_arc_set(g)
    ([(3, 2, 1)], 1)
    >>> min_feedback_arc_set(g, remove=True)  # Return DAG
    ([(1, 2, 2), (2, 3, 2), (3, 4, 2), (1, 3, 1), (2, 4, 1)], 1)
    """
    G = nx.DiGraph()
    edge_to_index = {}
    for i, (a, b, w) in enumerate(edges):
        G.add_edge(a, b)
        edge_to_index[a, b] = i

    nedges = len(edges)
    L = LPInstance()

    L.add_objective(edges, objective=MINIMIZE)

    constraints = []
    ncycles = 0
    for c in nx.simple_cycles(G):
        cycle_edges = []
        rc = c + [c[0]]  # Rotate the cycle
        for a, b in pairwise(rc):
            cycle_edges.append(edge_to_index[a, b])
        cc = summation(cycle_edges)
        constraints.append("{0} >= 1".format(cc))
        ncycles += 1
        if ncycles == maxcycles:
            break
    logging.debug("A total of {0} cycles found.".format(ncycles))

    L.constraints = constraints
    L.add_vars(nedges)

    selected, obj_val = L.lpsolve(clean=False)
    if remove:
        results = [x for i, x in enumerate(edges) if i not in selected] \
                        if selected else None
    else:
        results = [x for i, x in enumerate(edges) if i in selected] \
                        if selected else None

    return results, obj_val

if __name__ == '__main__':
    import doctest
    doctest.testmod()
