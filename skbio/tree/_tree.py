#!/usr/bin/env python

from __future__ import absolute_import, division, print_function

# ----------------------------------------------------------------------------
# Copyright (c) 2013--, scikit-bio development team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
# ----------------------------------------------------------------------------

import re
import warnings
from operator import or_
from copy import deepcopy
from itertools import combinations
from functools import reduce
from collections import defaultdict
from importlib import import_module

import numpy as np
from scipy.stats import pearsonr
from future.builtins import zip
from six import StringIO

from skbio.stats.distance import DistanceMatrix
from skbio.io import RecordError
from ._exception import (NoLengthError, DuplicateNodeError, NoParentError,
                         MissingNodeError, TreeError)

# This will be the responsibility of the ABC in the future.
import_module('skbio.io')


def distance_from_r(m1, m2):
    r"""Estimates distance as (1-r)/2: neg correl = max distance

    Parameters
    ----------
    m1 : DistanceMatrix
        a distance matrix to compare
    m2 : DistanceMatrix
        a distance matrix to compare

    Returns
    -------
    float
        The distance between m1 and m2

    """
    return (1-pearsonr(m1.data.flat, m2.data.flat)[0])/2


class TreeNode(object):
    r"""Representation of a node within a tree

    A `TreeNode` instance stores links to its parent and optional children
    nodes. In addition, the `TreeNode` can represent a `length` (e.g., a
    branch length) between itself and its parent. Within this object, the use
    of "children" and "descendants" is frequent in the documentation. A child
    is a direct descendant of a node, while descendants are all nodes that are
    below a given node (e.g., grand-children, etc).

    Parameters
    ----------
    name : str or None
        A node can have a name. It is common for tips in particular to have
        names, for instance, in a phylogenetic tree where the tips correspond
        to species.
    length : float, int, or None
        Distances between nodes can be used to represent evolutionary
        distances, time, etc.
    parent : TreeNode or None
        Connect this node to a parent
    children : list of TreeNode or None
        Connect this node to existing children

    Attributes
    ----------
    name
    length
    parent
    children
    id

    """
    default_write_format = 'newick'
    _exclude_from_copy = set(['parent', 'children', '_tip_cache',
                              '_non_tip_cache'])

    def __init__(self, name=None, length=None, parent=None, children=None):
        self.name = name
        self.length = length
        self.parent = parent
        self._tip_cache = {}
        self._non_tip_cache = {}
        self._registered_caches = set()

        self.children = []
        self.id = None

        if children is not None:
            self.extend(children)

    def __repr__(self):
        r"""Returns summary of the tree

        Returns
        -------
        str
            A summary of this node and all descendants

        Notes
        -----
        This method returns the name of the node and a count of tips and the
        number of internal nodes in the tree

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c, d)root;"))
        >>> repr(tree)
        '<TreeNode, name: root, internal node count: 1, tips count: 3>'

        .. shownumpydoc
        """
        nodes = [n for n in self.traverse(include_self=False)]
        n_tips = sum([n.is_tip() for n in nodes])
        n_nontips = len(nodes) - n_tips
        classname = self.__class__.__name__
        name = self.name if self.name is not None else "unnamed"

        return "<%s, name: %s, internal node count: %d, tips count: %d>" % \
               (classname, name, n_nontips, n_tips)

    def __str__(self):
        r"""Returns string version of self, with names and distances

        Returns
        -------
        str
            Returns a Newick representation of the tree

        See Also
        --------
        read
        write

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c);"))
        >>> str(tree)
        '((a,b)c);\n'

        .. shownumpydoc
        """

        fh = StringIO()
        self.write(fh)
        string = fh.getvalue()
        fh.close()
        return string

    def __iter__(self):
        r"""Node iter iterates over the `children`."""
        return iter(self.children)

    def __len__(self):
        return len(self.children)

    def __getitem__(self, i):
        r"""Node delegates slicing to `children`."""
        return self.children[i]

    def _adopt(self, node):
        r"""Update `parent` references but does NOT update `children`."""
        self.invalidate_caches()
        if node.parent is not None:
            node.parent.remove(node)
        node.parent = self
        return node

    def append(self, node):
        r"""Appends a node to `children`, in-place, cleaning up refs

        `append` will invalidate any node lookup caches, remove an existing
        parent on `node` if one exists, set the parent of `node` to self
        and add the `node` to `self` `children`.

        Parameters
        ----------
        node : TreeNode
            An existing TreeNode object

        See Also
        --------
        extend

        Examples
        --------
        >>> from skbio import TreeNode
        >>> root = TreeNode(name="root")
        >>> child1 = TreeNode(name="child1")
        >>> child2 = TreeNode(name="child2")
        >>> root.append(child1)
        >>> root.append(child2)
        >>> print(root)
        (child1,child2)root;
        <BLANKLINE>

        """
        self.children.append(self._adopt(node))

    def extend(self, nodes):
        r"""Append a `list` of `TreeNode` to `self`.

        `extend` will invalidate any node lookup caches, remove existing
        parents of the `nodes` if they have any, set their parents to self
        and add the nodes to `self` `children`.

        Parameters
        ----------
        nodes : list of TreeNode
            A list of TreeNode objects

        See Also
        --------
        append

        Examples
        --------
        >>> from skbio import TreeNode
        >>> root = TreeNode(name="root")
        >>> root.extend([TreeNode(name="child1"), TreeNode(name="child2")])
        >>> print(root)
        (child1,child2)root;
        <BLANKLINE>

        """
        self.children.extend([self._adopt(n) for n in nodes])

    def pop(self, index=-1):
        r"""Remove a `TreeNode` from `self`.

        Remove a child node by its index position. All node lookup caches
        are invalidated, and the parent reference for the popped node will be
        set to `None`.

        Parameters
        ----------
        index : int
            The index position in `children` to pop

        Returns
        -------
        TreeNode
            The popped child

        See Also
        --------
        remove
        remove_deleted

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("(a,b)c;"))
        >>> print(tree.pop(0))
        a;
        <BLANKLINE>

        """
        return self._remove_node(index)

    def _remove_node(self, idx):
        r"""The actual (and only) method that performs node removal"""
        self.invalidate_caches()
        node = self.children.pop(idx)
        node.parent = None
        return node

    def remove(self, node):
        r"""Remove a node from self

        Remove a `node` from `self` by identity of the node.

        Parameters
        ----------
        node : TreeNode
            The node to remove from self's children

        Returns
        -------
        bool
            `True` if the node was removed, `False` otherwise

        See Also
        --------
        pop
        remove_deleted

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("(a,b)c;"))
        >>> tree.remove(tree.children[0])
        True

        """
        for (i, curr_node) in enumerate(self.children):
            if curr_node is node:
                self._remove_node(i)
                return True
        return False

    def remove_deleted(self, func):
        r"""Delete nodes in which `func(node)` evaluates `True`.

        Remove all descendants from `self` that evaluate `True` from `func`.
        This has the potential to drop clades.

        Parameters
        ----------
        func : a function
            A function that evaluates `True` when a node should be deleted

        See Also
        --------
        pop
        remove

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("(a,b)c;"))
        >>> tree.remove_deleted(lambda x: x.name == 'b')
        >>> print(tree)
        (a)c;
        <BLANKLINE>
        """
        for node in self.traverse(include_self=False):
            if func(node):
                node.parent.remove(node)

    def prune(self):
        r"""Reconstructs correct topology after nodes have been removed.

        Internal nodes with only one child will be removed and new connections
        will be made to reflect change. This method is useful to call
        following node removals as it will clean up nodes with singular
        children.

        Names and properties of singular children will override the names and
        properties of their parents following the prune.

        Node lookup caches are invalidated.

        See Also
        --------
        shear
        remove
        pop
        remove_deleted

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f)root;"))
        >>> to_delete = tree.find('b')
        >>> tree.remove_deleted(lambda x: x == to_delete)
        >>> print(tree)
        ((a)c,(d,e)f)root;
        <BLANKLINE>
        >>> tree.prune()
        >>> print(tree)
        ((d,e)f,a)root;
        <BLANKLINE>

        """
        # build up the list of nodes to remove so the topology is not altered
        # while traversing
        nodes_to_remove = []
        for node in self.traverse(include_self=False):
            if len(node.children) == 1:
                nodes_to_remove.append(node)

        # clean up the single children nodes
        for node in nodes_to_remove:
            child = node.children[0]

            if child.length is None or node.length is None:
                child.length = child.length or node.length
            else:
                child.length += node.length

            node.parent.append(child)
            node.parent.remove(node)

    def shear(self, names):
        """Lop off tips until the tree just has the desired tip names.

        Parameters
        ----------
        names : Iterable of str
            The tip names on the tree to keep

        Returns
        -------
        TreeNode
            The resulting tree

        Raises
        ------
        ValueError
            If the names do not exist in the tree

        See Also
        --------
        prune
        remove
        pop
        remove_deleted

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> t = TreeNode.read(StringIO('((H:1,G:1):2,(R:0.5,M:0.7):3);'))
        >>> sheared = t.shear(['G', 'M'])
        >>> print(sheared.to_newick(with_distances=True))
        (G:3.0,M:3.7);

        """
        tcopy = self.deepcopy()
        all_tips = {n.name for n in tcopy.tips()}
        ids = set(names)

        if not ids.issubset(all_tips):
            raise ValueError("ids are not a subset of the tree!")

        while len(list(tcopy.tips())) != len(ids):
            for n in list(tcopy.tips()):
                if n.name not in ids:
                    n.parent.remove(n)

        tcopy.prune()

        return tcopy

    def copy(self):
        r"""Returns a copy of self using an iterative approach

        Perform an iterative deepcopy of self. It is not assured that the copy
        of node attributes will be performed iteratively as that depends on
        the copy method of the types being copied

        Returns
        -------
        TreeNode
            A new copy of self

        See Also
        --------
        unrooted_deepcopy
        unrooted_copy

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f)root;"))
        >>> tree_copy = tree.copy()
        >>> tree_nodes = set([id(n) for n in tree.traverse()])
        >>> tree_copy_nodes = set([id(n) for n in tree_copy.traverse()])
        >>> print(len(tree_nodes.intersection(tree_copy_nodes)))
        0

        """
        def __copy_node(node_to_copy):
            r"""Helper method to copy a node"""
            # this is _possibly_ dangerous, we're assuming the node to copy is
            # of the same class as self, and has the same exclusion criteria.
            # however, it is potentially dangerous to mix TreeNode subclasses
            # within a tree, so...
            result = self.__class__()
            efc = self._exclude_from_copy
            for key in node_to_copy.__dict__:
                if key not in efc:
                    result.__dict__[key] = deepcopy(node_to_copy.__dict__[key])
            return result

        root = __copy_node(self)
        nodes_stack = [[root, self, len(self.children)]]

        while nodes_stack:
            # check the top node, any children left unvisited?
            top = nodes_stack[-1]
            new_top_node, old_top_node, unvisited_children = top

            if unvisited_children:
                top[2] -= 1
                old_child = old_top_node.children[-unvisited_children]
                new_child = __copy_node(old_child)
                new_top_node.append(new_child)
                nodes_stack.append([new_child, old_child,
                                    len(old_child.children)])
            else:  # no unvisited children
                nodes_stack.pop()
        return root

    __copy__ = copy
    __deepcopy__ = deepcopy = copy

    def unrooted_deepcopy(self, parent=None):
        r"""Walks the tree unrooted-style and returns a new copy

        Perform a deepcopy of self and return a new copy of the tree as an
        unrooted copy. This is useful for defining new roots of the tree as
        the `TreeNode`.

        This method calls `TreeNode.unrooted_copy` which is recursive.

        Parameters
        ----------
        parent : TreeNode or None
            Used to avoid infinite loops when performing the unrooted traverse

        Returns
        -------
        TreeNode
            A new copy of the tree

        See Also
        --------
        copy
        unrooted_copy
        root_at

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,(b,c)d)e,(f,g)h)i;"))
        >>> new_tree = tree.find('d').unrooted_deepcopy()
        >>> print(new_tree)
        (b,c,(a,((f,g)h)e)d)root;
        <BLANKLINE>

        """
        root = self.root()
        root.assign_ids()

        new_tree = root.copy()
        new_tree.assign_ids()

        new_tree_self = new_tree.find_by_id(self.id)
        return new_tree_self.unrooted_copy(parent)

    def unrooted_copy(self, parent=None):
        r"""Walks the tree unrooted-style and returns a copy

        Perform a copy of self and return a new copy of the tree as an
        unrooted copy. This is useful for defining new roots of the tree as
        the `TreeNode`.

        This method is recursive.

        Warning, this is _NOT_ a deepcopy

        Parameters
        ----------
        parent : TreeNode or None
            Used to avoid infinite loops when performing the unrooted traverse

        Returns
        -------
        TreeNode
            A new copy of the tree

        See Also
        --------
        copy
        unrooted_deepcopy
        root_at

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,(b,c)d)e,(f,g)h)i;"))
        >>> new_tree = tree.find('d').unrooted_copy()
        >>> print(new_tree)
        (b,c,(a,((f,g)h)e)d)root;
        <BLANKLINE>

        """
        neighbors = self.neighbors(ignore=parent)
        children = [c.unrooted_copy(parent=self) for c in neighbors]

        # we might be walking UP the tree, so:
        if parent is None:
            # base edge
            edgename = None
            length = None
        elif parent.parent is self:
            # self's parent is becoming self's child
            edgename = parent.name
            length = parent.length
        else:
            assert parent is self.parent
            edgename = self.name
            length = self.length

        result = self.__class__(name=edgename, children=children,
                                length=length)

        if parent is None:
            result.name = "root"

        return result

    def count(self, tips=False):
        """Get the count of nodes in the tree

        Parameters
        ----------
        tips : bool
            If `True`, only return the count of the number of tips

        Returns
        -------
        int
            The number of nodes or tips

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,(b,c)d)e,(f,g)h)i;"))
        >>> print(tree.count())
        9
        >>> print(tree.count(tips=True))
        5

        """
        if tips:
            return len(list(self.tips()))
        else:
            return len(list(self.traverse(include_self=True)))

    def subtree(self, tip_list=None):
        r"""Make a copy of the subtree"""
        raise NotImplementedError()

    def subset(self):
        r"""Returns set of names that descend from specified node

        Get the set of `name` on tips that descend from this node.

        Returns
        -------
        frozenset
            The set of names at the tips of the clade that descends from self

        See Also
        --------
        subsets
        compare_subsets

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,(b,c)d)e,(f,g)h)i;"))
        >>> sorted(tree.subset())
        ['a', 'b', 'c', 'f', 'g']
        """
        return frozenset({i.name for i in self.tips()})

    def subsets(self):
        r"""Return all sets of names that come from self and its descendants

        Compute all subsets of tip names over `self`, or, represent a tree as a
        set of nested sets.

        Returns
        -------
        frozenset
            A frozenset of frozensets of str

        See Also
        --------
        subset
        compare_subsets

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("(((a,b)c,(d,e)f)h)root;"))
        >>> for s in sorted(tree.subsets()):
        ...     print(sorted(s))
        ['a', 'b']
        ['d', 'e']
        ['a', 'b', 'd', 'e']
        """
        sets = []
        for i in self.postorder(include_self=False):
            if not i.children:
                i.__leaf_set = frozenset([i.name])
            else:
                leaf_set = reduce(or_, [c.__leaf_set for c in i.children])
                if len(leaf_set) > 1:
                    sets.append(leaf_set)
                i.__leaf_set = leaf_set
        return frozenset(sets)

    def root_at(self, node):
        r"""Return a new tree rooted at the provided node.

        This can be useful for drawing unrooted trees with an orientation that
        reflects knowledge of the true root location.

        Parameters
        ----------
        node : TreeNode or str
            The node to root at

        Returns
        -------
        TreeNode
            A new copy of the tree

        Raises
        ------
        TreeError
            Raises a `TreeError` if a tip is specified as the new root

        See Also
        --------
        root_at_midpoint
        unrooted_deepcopy

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("(((a,b)c,(d,e)f)g,h)i;"))
        >>> print(tree.root_at('c'))
        (a,b,((d,e)f,(h)g)c)root;
        <BLANKLINE>

        """
        if isinstance(node, str):
            node = self.find(node)

        if not node.children:
            raise TreeError("Can't use a tip (%s) as the root" %
                            repr(node.name))
        return node.unrooted_deepcopy()

    def root_at_midpoint(self):
        r"""Return a new tree rooted at midpoint of the two tips farthest apart

        This method doesn't preserve the internal node naming or structure,
        but does keep tip to tip distances correct. Uses `unrooted_copy` but
        operates on a full copy of the tree.

        Raises
        ------
        TreeError
            If a tip ends up being the mid point

        Returns
        -------
        TreeNode
            A tree rooted at its midpoint
        LengthError
            Midpoint rooting requires `length` and will raise (indirectly) if
            evaluated nodes don't have length.

        See Also
        --------
        root_at
        unrooted_deepcopy

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("(((d:1,e:1,(g:1)f:1)c:1)b:1,h:1)"
        ...                               "a:1;"))
        >>> print(tree.root_at_midpoint())
        ((d:1.0,e:1.0,(g:1.0)f:1.0)c:0.5,((h:1.0)b:1.0):0.5)root;
        <BLANKLINE>

        """
        tree = self.copy()
        max_dist, tips = tree.get_max_distance()
        half_max_dist = max_dist / 2.0

        if max_dist == 0.0:  # only pathological cases with no lengths
            return tree

        tip1 = tree.find(tips[0])
        tip2 = tree.find(tips[1])
        lca = tree.lowest_common_ancestor([tip1, tip2])

        if tip1.accumulate_to_ancestor(lca) > half_max_dist:
            climb_node = tip1
        else:
            climb_node = tip2

        dist_climbed = 0.0
        while dist_climbed + climb_node.length < half_max_dist:
            dist_climbed += climb_node.length
            climb_node = climb_node.parent

        # now midpt is either at on the branch to climb_node's  parent
        # or midpt is at climb_node's parent
        if dist_climbed + climb_node.length == half_max_dist:
            # climb to midpoint spot
            climb_node = climb_node.parent
            if climb_node.is_tip():
                raise TreeError('error trying to root tree at tip')
            else:
                return climb_node.unrooted_copy()

        else:
            # make a new node on climb_node's branch to its parent
            old_br_len = climb_node.length

            new_root = tree.__class__()
            climb_node.parent.append(new_root)
            new_root.append(climb_node)

            climb_node.length = half_max_dist - dist_climbed
            new_root.length = old_br_len - climb_node.length

            return new_root.unrooted_copy()

    def is_tip(self):
        r"""Returns `True` if the current node has no `children`.

        Returns
        -------
        bool
            `True` if the node is a tip

        See Also
        --------
        is_root
        has_children

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c);"))
        >>> print(tree.is_tip())
        False
        >>> print(tree.find('a').is_tip())
        True

        """
        return not self.children

    def is_root(self):
        r"""Returns `True` if the current is a root, i.e. has no `parent`.

        Returns
        -------
        bool
            `True` if the node is the root

        See Also
        --------
        is_tip
        has_children

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c);"))
        >>> print(tree.is_root())
        True
        >>> print(tree.find('a').is_root())
        False

        """
        return self.parent is None

    def has_children(self):
        r"""Returns `True` if the node has `children`.

        Returns
        -------
        bool
            `True` if the node has children.

        See Also
        --------
        is_tip
        is_root

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c);"))
        >>> print(tree.has_children())
        True
        >>> print(tree.find('a').has_children())
        False

        """
        return not self.is_tip()

    def traverse(self, self_before=True, self_after=False, include_self=True):
        r"""Returns iterator over descendants

        This is a depth-first traversal. Since the trees are not binary,
        preorder and postorder traversals are possible, but inorder traversals
        would depend on the data in the tree and are not handled here.

        Parameters
        ----------
        self_before : bool
            includes each node before its descendants if True
        self_after : bool
            includes each node after its descendants if True
        include_self : bool
            include the initial node if True

        `self_before` and `self_after` are independent. If neither is `True`,
        only terminal nodes will be returned.

        Note that if self is terminal, it will only be included once even if
        `self_before` and `self_after` are both `True`.

        Returns
        -------
        GeneratorType
            Yields successive `TreeNode` objects

        See Also
        --------
        preorder
        postorder
        pre_and_postorder
        levelorder
        tips
        non_tips

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c);"))
        >>> for node in tree.traverse():
        ...     print(node.name)
        None
        c
        a
        b

        """
        if self_before:
            if self_after:
                return self.pre_and_postorder(include_self=include_self)
            else:
                return self.preorder(include_self=include_self)
        else:
            if self_after:
                return self.postorder(include_self=include_self)
            else:
                return self.tips(include_self=include_self)

    def preorder(self, include_self=True):
        r"""Performs preorder iteration over tree

        Parameters
        ----------
        include_self : bool
            include the initial node if True

        Returns
        -------
        GeneratorType
            Yields successive `TreeNode` objects

        See Also
        --------
        traverse
        postorder
        pre_and_postorder
        levelorder
        tips
        non_tips

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c);"))
        >>> for node in tree.preorder():
        ...     print(node.name)
        None
        c
        a
        b

        """
        stack = [self]
        while stack:
            curr = stack.pop()
            if include_self or (curr is not self):
                yield curr
            if curr.children:
                stack.extend(curr.children[::-1])

    def postorder(self, include_self=True):
        r"""Performs postorder iteration over tree.

        This is somewhat inelegant compared to saving the node and its index
        on the stack, but is 30% faster in the average case and 3x faster in
        the worst case (for a comb tree).

        Parameters
        ----------
        include_self : bool
            include the initial node if True

        Returns
        -------
        GeneratorType
            Yields successive `TreeNode` objects

        See Also
        --------
        traverse
        preorder
        pre_and_postorder
        levelorder
        tips
        non_tips

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c);"))
        >>> for node in tree.postorder():
        ...     print(node.name)
        a
        b
        c
        None

        """
        child_index_stack = [0]
        curr = self
        curr_children = self.children
        curr_children_len = len(curr_children)
        while 1:
            curr_index = child_index_stack[-1]
            # if there are children left, process them
            if curr_index < curr_children_len:
                curr_child = curr_children[curr_index]
                # if the current child has children, go there
                if curr_child.children:
                    child_index_stack.append(0)
                    curr = curr_child
                    curr_children = curr.children
                    curr_children_len = len(curr_children)
                    curr_index = 0
                # otherwise, yield that child
                else:
                    yield curr_child
                    child_index_stack[-1] += 1
            # if there are no children left, return self, and move to
            # self's parent
            else:
                if include_self or (curr is not self):
                    yield curr
                if curr is self:
                    break
                curr = curr.parent
                curr_children = curr.children
                curr_children_len = len(curr_children)
                child_index_stack.pop()
                child_index_stack[-1] += 1

    def pre_and_postorder(self, include_self=True):
        r"""Performs iteration over tree, visiting node before and after

        Parameters
        ----------
        include_self : bool
            include the initial node if True

        Returns
        -------
        GeneratorType
            Yields successive `TreeNode` objects

        See Also
        --------
        traverse
        postorder
        preorder
        levelorder
        tips
        non_tips

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c);"))
        >>> for node in tree.pre_and_postorder():
        ...     print(node.name)
        None
        c
        a
        b
        c
        None

        """
        # handle simple case first
        if not self.children:
            if include_self:
                yield self
            raise StopIteration
        child_index_stack = [0]
        curr = self
        curr_children = self.children
        while 1:
            curr_index = child_index_stack[-1]
            if not curr_index:
                if include_self or (curr is not self):
                    yield curr
            # if there are children left, process them
            if curr_index < len(curr_children):
                curr_child = curr_children[curr_index]
                # if the current child has children, go there
                if curr_child.children:
                    child_index_stack.append(0)
                    curr = curr_child
                    curr_children = curr.children
                    curr_index = 0
                # otherwise, yield that child
                else:
                    yield curr_child
                    child_index_stack[-1] += 1
            # if there are no children left, return self, and move to
            # self's parent
            else:
                if include_self or (curr is not self):
                    yield curr
                if curr is self:
                    break
                curr = curr.parent
                curr_children = curr.children
                child_index_stack.pop()
                child_index_stack[-1] += 1

    def levelorder(self, include_self=True):
        r"""Performs levelorder iteration over tree

        Parameters
        ----------
        include_self : bool
            include the initial node if True

        Returns
        -------
        GeneratorType
            Yields successive `TreeNode` objects

        See Also
        --------
        traverse
        postorder
        preorder
        pre_and_postorder
        tips
        non_tips

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f);"))
        >>> for node in tree.levelorder():
        ...     print(node.name)
        None
        c
        f
        a
        b
        d
        e

        """
        queue = [self]
        while queue:
            curr = queue.pop(0)
            if include_self or (curr is not self):
                yield curr
            if curr.children:
                queue.extend(curr.children)

    def tips(self, include_self=False):
        r"""Iterates over tips descended from `self`.

        Node order is consistent between calls and is ordered by a
        postorder traversal of the tree.

        Parameters
        ----------
        include_self : bool
            include the initial node if True

        Returns
        -------
        GeneratorType
            Yields successive `TreeNode` objects

        See Also
        --------
        traverse
        postorder
        preorder
        pre_and_postorder
        levelorder
        non_tips

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f);"))
        >>> for node in tree.tips():
        ...     print(node.name)
        a
        b
        d
        e

        """
        for n in self.postorder(include_self=False):
            if n.is_tip():
                yield n

    def non_tips(self, include_self=False):
        r"""Iterates over nontips descended from self

        `include_self`, if `True` (default is False), will return the current
        node as part of non_tips if it is a non_tip. Node order is consistent
        between calls and is ordered by a postorder traversal of the tree.


        Parameters
        ----------
        include_self : bool
            include the initial node if True

        Returns
        -------
        GeneratorType
            Yields successive `TreeNode` objects

        See Also
        --------
        traverse
        postorder
        preorder
        pre_and_postorder
        levelorder
        tips

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f);"))
        >>> for node in tree.non_tips():
        ...     print(node.name)
        c
        f

        """
        for n in self.postorder(include_self):
            if not n.is_tip():
                yield n

    def invalidate_caches(self, attr=True):
        r"""Delete lookup and attribute caches

        Parameters
        ----------
        attr : bool, optional
            If ``True``, invalidate attribute caches created by
            `TreeNode.cache_attr`.

        See Also
        --------
        create_caches
        cache_attr
        find

        """
        if not self.is_root():
            self.root().invalidate_caches()
        else:
            self._tip_cache = {}
            self._non_tip_cache = {}

            if self._registered_caches and attr:
                for n in self.traverse():
                    for cache in self._registered_caches:
                        if hasattr(n, cache):
                            delattr(n, cache)

    def create_caches(self):
        r"""Construct an internal lookups to facilitate searching by name

        This method will not cache nodes in which the .name is None. This
        method will raise `DuplicateNodeError` if a name conflict in the tips
        is discovered, but will not raise if on internal nodes. This is
        because, in practice, the tips of a tree are required to be unique
        while no such requirement holds for internal nodes.

        Raises
        ------
        DuplicateNodeError
            The tip cache requires that names are unique (with the exception of
            names that are None)

        See Also
        --------
        invalidate_caches
        cache_attr
        find

        """
        if not self.is_root():
            self.root().create_caches()
        else:
            if self._tip_cache and self._non_tip_cache:
                return

            self.invalidate_caches(attr=False)

            tip_cache = {}
            non_tip_cache = defaultdict(list)

            for node in self.postorder():
                name = node.name

                if name is None:
                    continue

                if node.is_tip():
                    if name in tip_cache:
                        raise DuplicateNodeError("Tip with name '%s' already "
                                                 "exists!" % name)

                    tip_cache[name] = node
                else:
                    non_tip_cache[name].append(node)

            self._tip_cache = tip_cache
            self._non_tip_cache = non_tip_cache

    def find_all(self, name):
        r"""Find all nodes that match `name`

        The first call to `find_all` will cache all nodes in the tree on the
        assumption that additional calls to `find_all` will be made.

        Parameters
        ----------
        name : TreeNode or str
            The name or node to find. If `name` is `TreeNode` then all other
            nodes with the same name will be returned.

        Raises
        ------
        MissingNodeError
            Raises if the node to be searched for is not found

        Returns
        -------
        list of TreeNode
            The nodes found

        See Also
        --------
        find
        find_by_id
        find_by_func

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio.tree import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)d,(f,g)c);"))
        >>> for node in tree.find_all('c'):
        ...     print(node.name, node.children[0].name, node.children[1].name)
        c a b
        c f g
        >>> for node in tree.find_all('d'):
        ...     print(node.name, str(node))
        d (d,e)d;
        <BLANKLINE>
        d d;
        <BLANKLINE>
        """
        root = self.root()

        # if what is being passed in looks like a node, just return it
        if isinstance(name, root.__class__):
            return [name]

        root.create_caches()

        tip = root._tip_cache.get(name, None)
        nodes = root._non_tip_cache.get(name, [])

        nodes.append(tip) if tip is not None else None

        if not nodes:
            raise MissingNodeError("Node %s is not in self" % name)
        else:
            return nodes

    def find(self, name):
        r"""Find a node by `name`.

        The first call to `find` will cache all nodes in the tree on the
        assumption that additional calls to `find` will be made.

        `find` will first attempt to find the node in the tips. If it cannot
        find a corresponding tip, then it will search through the internal
        nodes of the tree. In practice, phylogenetic trees and other common
        trees in biology do not have unique internal node names. As a result,
        this find method will only return the first occurance of an internal
        node encountered on a postorder traversal of the tree.

        Parameters
        ----------
        name : TreeNode or str
            The name or node to find. If `name` is `TreeNode` then it is
            simply returned

        Raises
        ------
        MissingNodeError
            Raises if the node to be searched for is not found

        Returns
        -------
        TreeNode
            The found node

        See Also
        --------
        find_all
        find_by_id
        find_by_func

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f);"))
        >>> print(tree.find('c').name)
        c
        """
        root = self.root()

        # if what is being passed in looks like a node, just return it
        if isinstance(name, root.__class__):
            return name

        root.create_caches()
        node = root._tip_cache.get(name, None)

        if node is None:
            node = root._non_tip_cache.get(name, [None])[0]

        if node is None:
            raise MissingNodeError("Node %s is not in self" % name)
        else:
            return node

    def find_by_id(self, node_id):
        r"""Find a node by `id`.

        This search method is based from the root.

        Parameters
        ----------
        node_id : int
            The `id` of a node in the tree

        Returns
        -------
        TreeNode
            The tree node with the matcing id

        Notes
        -----
        This method does not cache id associations. A full traversal of the
        tree is performed to find a node by an id on every call.

        Raises
        ------
        MissingNodeError
            This method will raise if the `id` cannot be found

        See Also
        --------
        find
        find_all
        find_by_func

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f);"))
        >>> print(tree.find_by_id(2).name)
        d

        """
        # if this method gets used frequently, then we should cache by ID
        # as well
        root = self.root()
        root.assign_ids()

        node = None
        for n in self.traverse(include_self=True):
            if n.id == node_id:
                node = n
                break

        if node is None:
            raise MissingNodeError("ID %d is not in self" % node_id)
        else:
            return node

    def find_by_func(self, func):
        r"""Find all nodes given a function

        This search method is based on the current subtree, not the root.

        Parameters
        ----------
        func : a function
            A function that accepts a TreeNode and returns `True` or `Fals`,
            where `True` indicates the node is to be yielded

        Returns
        -------
        GeneratorType
            A generator that yields nodes

        See Also
        --------
        find
        find_all
        find_by_id

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f);"))
        >>> func = lambda x: x.parent == tree.find('c')
        >>> [n.name for n in tree.find_by_func(func)]
        ['a', 'b']
        """
        for node in self.traverse(include_self=True):
            if func(node):
                yield node

    def ancestors(self):
        r"""Returns all ancestors back to the root

        This call will return all nodes in the path back to root, but does not
        include the node instance that the call was made from.

        Returns
        -------
        list of TreeNode
            The path, toward the root, from self

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f)root;"))
        >>> [node.name for node in tree.find('a').ancestors()]
        ['c', 'root']

        """
        result = []
        curr = self
        while not curr.is_root():
            result.append(curr.parent)
            curr = curr.parent

        return result

    def root(self):
        r"""Returns root of the tree `self` is in

        Returns
        -------
        TreeNode
            The root of the tree

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f)root;"))
        >>> tip_a = tree.find('a')
        >>> root = tip_a.root()
        >>> root == tree
        True

        """
        curr = self
        while not curr.is_root():
            curr = curr.parent
        return curr

    def siblings(self):
        r"""Returns all nodes that are `children` of `self` `parent`.

        This call excludes `self` from the list.

        Returns
        -------
        list of TreeNode
            The list of sibling nodes relative to self

        See Also
        --------
        neighbors

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e,f)g)root;"))
        >>> tip_e = tree.find('e')
        >>> [n.name for n in tip_e.siblings()]
        ['d', 'f']

        """
        if self.is_root():
            return []

        result = self.parent.children[:]
        result.remove(self)

        return result

    def neighbors(self, ignore=None):
        r"""Returns all nodes that are connected to self

        This call does not include `self` in the result

        Parameters
        ----------
        ignore : TreeNode
            A node to ignore

        Returns
        -------
        list of TreeNode
            The list of all nodes that are connected to self

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f)root;"))
        >>> node_c = tree.find('c')
        >>> [n.name for n in node_c.neighbors()]
        ['a', 'b', 'root']

        """
        nodes = [n for n in self.children + [self.parent] if n is not None]
        if ignore is None:
            return nodes
        else:
            return [n for n in nodes if n is not ignore]

    def lowest_common_ancestor(self, tipnames):
        r"""Lowest common ancestor for a list of tips

        Parameters
        ----------
        tipnames : list of TreeNode or str
            The nodes of interest

        Returns
        -------
        TreeNode
            The lowest common ancestor of the passed in nodes

        Raises
        ------
        ValueError
            If no tips could be found in the tree

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f)root;"))
        >>> nodes = [tree.find('a'), tree.find('b')]
        >>> lca = tree.lowest_common_ancestor(nodes)
        >>> print(lca.name)
        c
        >>> nodes = [tree.find('a'), tree.find('e')]
        >>> lca = tree.lca(nodes)  # lca is an alias for convience
        >>> print(lca.name)
        root

        """
        if len(tipnames) == 1:
            return self.find(tipnames[0])

        tips = [self.find(name) for name in tipnames]

        if len(tips) == 0:
            raise ValueError("No tips found!")

        nodes_to_scrub = []

        for t in tips:
            if t.is_root():
                # has to be the LCA...
                return t

            prev = t
            curr = t.parent

            while curr and not hasattr(curr, 'black'):
                setattr(curr, 'black', [prev])
                nodes_to_scrub.append(curr)
                prev = curr
                curr = curr.parent

            # increase black count, multiple children lead to here
            if curr:
                curr.black.append(prev)

        curr = self
        while len(curr.black) == 1:
            curr = curr.black[0]

        # clean up tree
        for n in nodes_to_scrub:
            delattr(n, 'black')

        return curr

    lca = lowest_common_ancestor  # for convenience

    @classmethod
    def from_taxonomy(cls, lineage_map):
        """Construct a tree from a taxonomy

        Parameters
        ----------
        lineage_map : iterable of tuple
            A id to lineage mapping where the first index is an ID and the
            second index is an iterable of the lineage.

        Returns
        -------
        TreeNode
            The constructed taxonomy

        Examples
        --------
        >>> from skbio.tree import TreeNode
        >>> lineages = {'1': ['Bacteria', 'Firmicutes', 'Clostridia'],
        ...             '2': ['Bacteria', 'Firmicutes', 'Bacilli'],
        ...             '3': ['Bacteria', 'Bacteroidetes', 'Sphingobacteria'],
        ...             '4': ['Archaea', 'Euryarchaeota', 'Thermoplasmata'],
        ...             '5': ['Archaea', 'Euryarchaeota', 'Thermoplasmata'],
        ...             '6': ['Archaea', 'Euryarchaeota', 'Halobacteria'],
        ...             '7': ['Archaea', 'Euryarchaeota', 'Halobacteria'],
        ...             '8': ['Bacteria', 'Bacteroidetes', 'Sphingobacteria'],
        ...             '9': ['Bacteria', 'Bacteroidetes', 'Cytophagia']}
        >>> tree = TreeNode.from_taxonomy(lineages.items())
        >>> print(tree.ascii_art())
                                      /Clostridia-1
                            /Firmicutes
                           |          \Bacilli- /-2
                  /Bacteria|
                 |         |                    /-3
                 |         |          /Sphingobacteria
                 |          \Bacteroidetes      \-8
                 |                   |
        ---------|                    \Cytophagia-9
                 |
                 |                              /-5
                 |                    /Thermoplasmata
                 |                   |          \-4
                  \Archaea- /Euryarchaeota
                                     |          /-7
                                      \Halobacteria
                                                \-6

        """
        root = cls(name=None)
        root._lookup = {}

        for id_, lineage in lineage_map:
            cur_node = root

            # for each name, see if we've seen it, if not, add that puppy on
            for name in lineage:
                if name in cur_node._lookup:
                    cur_node = cur_node._lookup[name]
                else:
                    new_node = TreeNode(name=name)
                    new_node._lookup = {}
                    cur_node._lookup[name] = new_node
                    cur_node.append(new_node)
                    cur_node = new_node

            cur_node.append(TreeNode(name=id_))

        # scrub the lookups
        for node in root.non_tips(include_self=True):
            del node._lookup

        return root

    @classmethod
    def from_file(cls, tree_f):
        """Load a tree from a file or file-like object

        .. note:: Deprecated in scikit-bio 0.2.0-dev
           ``from_file`` will be removed in scikit-bio 0.3.0. It is replaced
           by ``read``, which is a more general method for deserializing
           TreeNode instances. ``read`` supports multiple file formats,
           automatic file format detection, etc. by taking advantage of
           scikit-bio's I/O registry system. See :mod:`skbio.io` for more
           details.

        """
        warnings.warn(
            "TreeNode.from_file is deprecated and will be removed in "
            "scikit-bio 0.3.0. Please update your code to use TreeNode.read.",
            UserWarning)
        return cls.read(tree_f, format='newick')

    def _balanced_distance_to_tip(self):
        """Return the distance to tip from this node.

        The distance to every tip from this node must be equal for this to
        return a correct result.

        Returns
        -------
        int
            The distance to tip of a length-balanced tree

        """
        node = self
        distance = 0
        while node.has_children():
            distance += node.children[0].length
            node = node.children[0]
        return distance

    @classmethod
    def from_linkage_matrix(cls, linkage_matrix, id_list):
        """Return tree from SciPy linkage matrix.

        Parameters
        ----------
        linkage_matrix : ndarray
            A SciPy linkage matrix as returned by
            `scipy.cluster.hierarchy.linkage`
        id_list : list
            The indices of the `id_list` will be used in the linkage_matrix

        Returns
        -------
        TreeNode
            An unrooted bifurcated tree

        See Also
        --------
        scipy.cluster.hierarchy.linkage

        """
        tip_width = len(id_list)
        cluster_count = len(linkage_matrix)
        lookup_len = cluster_count + tip_width
        node_lookup = np.empty(lookup_len, dtype=TreeNode)

        for i, name in enumerate(id_list):
            node_lookup[i] = TreeNode(name=name)

        for i in range(tip_width, lookup_len):
            node_lookup[i] = TreeNode()

        newest_cluster_index = cluster_count + 1
        for link in linkage_matrix:
            child_a = node_lookup[int(link[0])]
            child_b = node_lookup[int(link[1])]

            path_length = link[2] / 2
            child_a.length = path_length - child_a._balanced_distance_to_tip()
            child_b.length = path_length - child_b._balanced_distance_to_tip()

            new_cluster = node_lookup[newest_cluster_index]
            new_cluster.append(child_a)
            new_cluster.append(child_b)

            newest_cluster_index += 1

        return node_lookup[-1]

    @classmethod
    def from_newick(cls, lines, unescape_name=True):
        r"""Returns tree from the Clustal .dnd file format and equivalent

        .. note:: Deprecated in scikit-bio 0.2.0-dev
           ``from_newick`` will be removed in scikit-bio 0.3.0. It is replaced
           by ``read``, which is a more general method for deserializing
           TreeNode instances. ``read`` supports multiple file formats,
           automatic file format detection, etc. by taking advantage of
           scikit-bio's I/O registry system. See :mod:`skbio.io` for more
           details.

        The tree is made of `skbio.TreeNode` objects, with branch
        lengths if specified by the format.

        More information on the Newick format can be found here [1]. In brief,
        the format uses parentheses to define nesting. For instance, a three
        taxon tree can be represented with::

            ((a,b),c);

        Two possible ways to represent this tree drawing it out would be::

               *
              / \
             *   \
            / \   \
            a b   c

            a
             \__|___ c
             /
            b

        The Newick format allows for defining branch length as well, for
        example::

            ((a:0.1,b:0.2):0.3,c:0.4);

        This structure has a the same topology as the first example but the
        tree now contains more information about how similar or dissimilar
        nodes are to their parents. In the above example, we can see that tip
        `a` has a distance of 0.1 to its parent, and `b` has a distance of 0.2
        to its parent. We can additionally see that the clade that encloses
        tips `a` and `b` has a distance of 0.3 to its parent, or in this case,
        the root.

        Parameters
        ----------
        lines : a str, a list of str, or a file-like object
            The input newick string to parse
        unescape_names : bool
            Remove extraneous quote marks around names. Sometimes other
            programs are sensitive to the characters used in names, and it
            is essential (at times) to quote node names for compatibility.

        Returns
        -------
        TreeNode
            The root of the parsed tree

        Raises
        ------
        RecordError
            The following three conditions will trigger a `RecordError`:
                * Unbalanced number of left and right parentheses
                * A malformed newick string. For instance, if a semicolon is
                    embedded within the string as opposed to at the end.
                * If a non-newick string is passed.

        See Also
        --------
        to_newick

        Examples
        --------
        >>> from skbio import TreeNode
        >>> TreeNode.from_newick("((a,b)c,(d,e)f)root;")
        <TreeNode, name: root, internal node count: 2, tips count: 4>
        >>> from six import StringIO
        >>> s = StringIO("((a,b),c);")
        >>> TreeNode.from_newick(s)
        <TreeNode, name: unnamed, internal node count: 1, tips count: 3>

        References
        ----------
        [1] http://evolution.genetics.washington.edu/phylip/newicktree.html

        """
        warnings.warn(
            "TreeNode.from_newick is deprecated and will be removed in "
            "scikit-bio 0.3.0. Please update your code to use TreeNode.read.",
            UserWarning)

        def _new_child(old_node):
            """Returns new_node which has old_node as its parent."""
            new_node = cls()
            new_node.parent = old_node
            if old_node is not None:
                if new_node not in old_node.children:
                    old_node.children.append(new_node)
            return new_node

        if isinstance(lines, str):
            data = lines
        else:
            data = ''.join(lines)

        # skip arb comment stuff if present: start at first paren
        paren_index = data.find('(')
        data = data[paren_index:]
        left_count = data.count('(')
        right_count = data.count(')')

        if left_count != right_count:
            raise RecordError("Found %s left parens but %s right parens." %
                              (left_count, right_count))

        curr_node = None
        state = 'PreColon'
        state1 = 'PreClosed'
        last_token = None

        for t in _dnd_tokenizer(data):
            if t == ':':
                # expecting branch length
                state = 'PostColon'
                # prevent state reset
                last_token = t
                continue
            if t == ')' and last_token in ',(':
                # node without name
                new_node = _new_child(curr_node)
                new_node.name = None
                curr_node = new_node.parent
                state1 = 'PostClosed'
                last_token = t
                continue
            if t == ')':
                # closing the current node
                curr_node = curr_node.parent
                state1 = 'PostClosed'
                last_token = t
                continue
            if t == '(':
                # opening a new node
                curr_node = _new_child(curr_node)
            elif t == ';':  # end of data
                last_token = t
                break
            elif t == ',' and last_token in ',(':
                # node without name
                new_node = _new_child(curr_node)
                new_node.name = None
                curr_node = new_node.parent
            elif t == ',':
                # separator: next node adds to this node's parent
                curr_node = curr_node.parent
            elif state == 'PreColon' and state1 == 'PreClosed':
                # data for the current node
                new_node = _new_child(curr_node)
                if unescape_name:
                    if t.startswith("'") and t.endswith("'"):
                        while t.startswith("'") and t.endswith("'"):
                            t = t[1:-1]
                    else:
                        if '_' in t:
                            t = t.replace('_', ' ')
                new_node.name = t
                curr_node = new_node
            elif state == 'PreColon' and state1 == 'PostClosed':
                if unescape_name:
                    while t.startswith("'") and t.endswith("'"):
                        t = t[1:-1]
                curr_node.name = t
            elif state == 'PostColon':
                # length data for the current node
                curr_node.length = float(t)
            else:
                # can't think of a reason to get here
                raise RecordError("Incorrect PhyloNode state? %s" % t)
            state = 'PreColon'  # get here for any non-colon token
            state1 = 'PreClosed'
            last_token = t

        if curr_node is not None and curr_node.parent is not None:
            raise RecordError("Didn't get back to root of tree. The newick "
                              "string may be malformed.")

        if curr_node is None:  # no data -- return empty node
            return cls()
        return curr_node  # this should be the root of the tree

    def to_taxonomy(self, allow_empty=False, filter_f=None):
        """Returns a taxonomy representation of self

        Parameters
        ----------
        allow_empty : bool, optional
            Allow gaps the taxonomy (e.g., internal nodes without names).
        filter_f : function, optional
            Specify a filtering function that returns True if the lineage is
            to be returned. This function must accept a ``TreeNode`` as its
            first parameter, and a ``list`` that represents the lineage as the
            second parameter.

        Returns
        -------
        generator
            (tip, [lineage]) where tip corresponds to a tip in the tree and
            the [lineage] is the expanded names from root to tip. Nones and
            empty strings are omitted from the lineage

        Notes
        -----
        If ``allow_empty`` is ``True`` and the root node does not have a name,
        then that name will not be included. This is because it is common to
        have multiple domains represented in the taxonomy, which would result
        in a root node that does not have a name and does not make sense to
        represent in the output.

        Examples
        --------
        >>> from skbio.tree import TreeNode
        >>> lineages = {'1': ['Bacteria', 'Firmicutes', 'Clostridia'],
        ...             '2': ['Bacteria', 'Firmicutes', 'Bacilli'],
        ...             '3': ['Bacteria', 'Bacteroidetes', 'Sphingobacteria'],
        ...             '4': ['Archaea', 'Euryarchaeota', 'Thermoplasmata'],
        ...             '5': ['Archaea', 'Euryarchaeota', 'Thermoplasmata'],
        ...             '6': ['Archaea', 'Euryarchaeota', 'Halobacteria'],
        ...             '7': ['Archaea', 'Euryarchaeota', 'Halobacteria'],
        ...             '8': ['Bacteria', 'Bacteroidetes', 'Sphingobacteria'],
        ...             '9': ['Bacteria', 'Bacteroidetes', 'Cytophagia']}
        >>> tree = TreeNode.from_taxonomy(lineages.items())
        >>> lineages = sorted([(n.name, l) for n, l in tree.to_taxonomy()])
        >>> for name, lineage in lineages:
        ...     print(name, '; '.join(lineage))
        1 Bacteria; Firmicutes; Clostridia
        2 Bacteria; Firmicutes; Bacilli
        3 Bacteria; Bacteroidetes; Sphingobacteria
        4 Archaea; Euryarchaeota; Thermoplasmata
        5 Archaea; Euryarchaeota; Thermoplasmata
        6 Archaea; Euryarchaeota; Halobacteria
        7 Archaea; Euryarchaeota; Halobacteria
        8 Bacteria; Bacteroidetes; Sphingobacteria
        9 Bacteria; Bacteroidetes; Cytophagia

        """
        if filter_f is None:
            filter_f = lambda a, b: True

        self.assign_ids()
        seen = set()
        lineage = []

        # visit internal nodes while traversing out to the tips, and on the
        # way back up
        for node in self.traverse(self_before=True, self_after=True):
            if node.is_tip():
                if filter_f(node, lineage):
                    yield (node, lineage[:])
            else:
                if allow_empty:
                    if node.is_root() and not node.name:
                        continue
                else:
                    if not node.name:
                        continue

                if node.id in seen:
                    lineage.pop(-1)
                else:
                    lineage.append(node.name)
                    seen.add(node.id)

    def to_array(self, attrs=None):
        """Return an array representation of self

        Parameters
        ----------
        attrs : list of tuple or None
            The attributes and types to return. The expected form is
            [(attribute_name, type)]. If `None`, then `name`, `length`, and
            `id` are returned.

        Returns
        -------
        dict of array
            {id_index: {id: TreeNode},
             child_index: [(node_id, left_child_id, right_child_id)],
             attr_1: array(...),
             ...
             attr_N: array(...)}

        Notes
        -----
        Attribute arrays are in index order such that TreeNode.id can be used
        as a lookup into the the array

        If `length` is an attribute, this will also record the length off the
        root which is `nan`. Take care when summing.

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> t = TreeNode.read(StringIO('(((a:1,b:2,c:3)x:4,(d:5)y:6)z:7);'))
        >>> res = t.to_array()
        >>> res.keys()
        ['child_index', 'length', 'name', 'id_index', 'id']
        >>> res['child_index']
        [(4, 0, 2), (5, 3, 3), (6, 4, 5), (7, 6, 6)]
        >>> for k, v in res['id_index'].items():
        ...     print(k, v)
        ...
        0 a:1.0;
        <BLANKLINE>
        1 b:2.0;
        <BLANKLINE>
        2 c:3.0;
        <BLANKLINE>
        3 d:5.0;
        <BLANKLINE>
        4 (a:1.0,b:2.0,c:3.0)x:4.0;
        <BLANKLINE>
        5 (d:5.0)y:6.0;
        <BLANKLINE>
        6 ((a:1.0,b:2.0,c:3.0)x:4.0,(d:5.0)y:6.0)z:7.0;
        <BLANKLINE>
        7 (((a:1.0,b:2.0,c:3.0)x:4.0,(d:5.0)y:6.0)z:7.0);
        <BLANKLINE>
        >>> res['id']
        array([0, 1, 2, 3, 4, 5, 6, 7])
        >>> res['name']
        array(['a', 'b', 'c', 'd', 'x', 'y', 'z', None], dtype=object)

        """
        if attrs is None:
            attrs = [('name', object), ('length', float), ('id', int)]
        else:
            for attr, dtype in attrs:
                if not hasattr(self, attr):
                    raise AttributeError("Invalid attribute '%s'." % attr)

        id_index, child_index = self.index_tree()
        n = self.id + 1  # assign_ids starts at 0
        tmp = [np.zeros(n, dtype=dtype) for attr, dtype in attrs]

        for node in self.traverse(include_self=True):
            n_id = node.id
            for idx, (attr, dtype) in enumerate(attrs):
                tmp[idx][n_id] = getattr(node, attr)

        results = {'id_index': id_index, 'child_index': child_index}
        results.update({attr: arr for (attr, dtype), arr in zip(attrs, tmp)})
        return results

    def to_newick(self, with_distances=False, semicolon=True,
                  escape_name=True):
        r"""Return the newick string representation of this tree.

        .. note:: Deprecated in scikit-bio 0.2.0-dev
           ``to_newick`` will be removed in scikit-bio 0.3.0. It is replaced by
           ``write``, which is a more general method for serializing TreeNode
           instances. ``write`` supports multiple file formats by taking
           advantage of scikit-bio's I/O registry system. See :mod:`skbio.io`
           for more details.

        Please see `TreeNode.from_newick` for a further description of the
        Newick format.

        Parameters
        ----------
        with_distances : bool
            If true, include lengths between nodes
        semicolon : bool
            If true, terminate the tree string with a semicolon
        escape_name : bool
            If true, wrap node names that include []'"(),:;_ in single quotes

        Returns
        -------
        str
            A Newick string representation of the tree

        See Also
        --------
        from_newick

        Examples
        --------
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f)root;"))
        >>> print(tree.to_newick())
        ((a,b)c,(d,e)f)root;

        """
        warnings.warn(
            "TreeNode.to_newick is deprecated and will be removed in "
            "scikit-bio 0.3.0. Please update your code to use TreeNode.write.",
            UserWarning)
        result = ['(']
        nodes_stack = [[self, len(self.children)]]
        node_count = 1

        while nodes_stack:
            node_count += 1
            # check the top node, any children left unvisited?
            top = nodes_stack[-1]
            top_node, num_unvisited_children = top
            if num_unvisited_children:  # has any child unvisited
                top[1] -= 1  # decrease the #of children unvisited
                next_child = top_node.children[-num_unvisited_children]
                # pre-visit
                if next_child.children:
                    result.append('(')
                nodes_stack.append([next_child, len(next_child.children)])
            else:  # no unvisited children
                nodes_stack.pop()
                # post-visit
                if top_node.children:
                    result[-1] = ')'

                if top_node.name is None:
                    name = ''
                else:
                    name = str(top_node.name)
                    if escape_name and not (name.startswith("'") and
                                            name.endswith("'")):
                        if re.search("""[]['"(),:;_]""", name):
                            name = "'%s'" % name.replace("'", "''")
                        else:
                            name = name.replace(' ', '_')
                result.append(name)

                if with_distances and top_node.length is not None:
                    result[-1] = "%s:%s" % (result[-1], top_node.length)

                result.append(',')

        if len(result) <= 3:  # single node with or without name
            if semicolon:
                return "%s;" % result[1]
            else:
                return result[1]
        else:
            if semicolon:
                result[-1] = ';'
            else:
                result.pop(-1)
            return ''.join(result)

    def _ascii_art(self, char1='-', show_internal=True, compact=False):
        LEN = 10
        PAD = ' ' * LEN
        PA = ' ' * (LEN - 1)
        namestr = self.name or ''  # prevents name of NoneType
        if self.children:
            mids = []
            result = []
            for c in self.children:
                if c is self.children[0]:
                    char2 = '/'
                elif c is self.children[-1]:
                    char2 = '\\'
                else:
                    char2 = '-'
                (clines, mid) = c._ascii_art(char2, show_internal, compact)
                mids.append(mid + len(result))
                result.extend(clines)
                if not compact:
                    result.append('')
            if not compact:
                result.pop()
            (lo, hi, end) = (mids[0], mids[-1], len(result))
            prefixes = [PAD] * (lo + 1) + [PA + '|'] * \
                (hi - lo - 1) + [PAD] * (end - hi)
            mid = np.int(np.trunc((lo + hi) / 2))
            prefixes[mid] = char1 + '-' * (LEN - 2) + prefixes[mid][-1]
            result = [p + l for (p, l) in zip(prefixes, result)]
            if show_internal:
                stem = result[mid]
                result[mid] = stem[0] + namestr + stem[len(namestr) + 1:]
            return (result, mid)
        else:
            return ([char1 + '-' + namestr], 0)

    def ascii_art(self, show_internal=True, compact=False):
        r"""Returns a string containing an ascii drawing of the tree

        Note, this method calls a private recursive function and is not safe
        for large trees.

        Parameters
        ----------
        show_internal : bool
            includes internal edge names
        compact : bool
            use exactly one line per tip

        Returns
        -------
        str
            an ASCII formatted version of the tree

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b)c,(d,e)f)root;"))
        >>> print(tree.ascii_art())
                            /-a
                  /c-------|
                 |          \-b
        -root----|
                 |          /-d
                  \f-------|
                            \-e
        """
        (lines, mid) = self._ascii_art(show_internal=show_internal,
                                       compact=compact)
        return '\n'.join(lines)

    def accumulate_to_ancestor(self, ancestor):
        r"""Return the sum of the distance between self and ancestor

        Parameters
        ----------
        ancestor : TreeNode
            The node of the ancestor to accumulate distance too

        Returns
        -------
        float
            The sum of lengths between self and ancestor

        Raises
        ------
        NoParentError
            A NoParentError is raised if the ancestor is not an ancestor of
            self
        NoLengthError
            A NoLengthError is raised if one of the nodes between self and
            ancestor (including self) lacks a `length` attribute

        See Also
        --------
        distance

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a:1,b:2)c:3,(d:4,e:5)f:6)root;"))
        >>> root = tree
        >>> tree.find('a').accumulate_to_ancestor(root)
        4.0
        """
        accum = 0.0
        curr = self
        while curr is not ancestor:
            if curr.is_root():
                raise NoParentError("Provided ancestor is not in the path")

            if curr.length is None:
                raise NoLengthError("No length on node %s found!" %
                                    curr.name or "unnamed")

            accum += curr.length
            curr = curr.parent

        return accum

    def distance(self, other):
        """Return the distance between self and other

        This method can be used to compute the distances between two tips,
        however, it is not optimized for computing pairwise tip distances.

        Parameters
        ----------
        other : TreeNode
            The node to compute a distance to

        Returns
        -------
        float
            The distance between two nodes

        Raises
        ------
        NoLengthError
            A NoLengthError will be raised if a node without `length` is
            encountered

        See Also
        --------
        tip_tip_distances
        accumulate_to_ancestor
        compare_tip_distances
        get_max_distance

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a:1,b:2)c:3,(d:4,e:5)f:6)root;"))
        >>> tip_a = tree.find('a')
        >>> tip_d = tree.find('d')
        >>> tip_a.distance(tip_d)
        14.0
        """
        if self is other:
            return 0.0

        root = self.root()
        lca = root.lowest_common_ancestor([self, other])
        accum = self.accumulate_to_ancestor(lca)
        accum += other.accumulate_to_ancestor(lca)

        return accum

    def _set_max_distance(self):
        """Propagate tip distance information up the tree

        This method was originally implemented by Julia Goodrich with the
        intent of being able to determine max tip to tip distances between
        nodes on large trees efficiently. The code has been modified to track
        the specific tips the distance is between
        """
        for n in self.postorder():
            if n.is_tip():
                n.MaxDistTips = [[0.0, n], [0.0, n]]
            else:
                if len(n.children) == 1:
                    raise TreeError("No support for single descedent nodes")
                else:
                    tip_info = [(max(c.MaxDistTips), c) for c in n.children]
                    dists = [i[0][0] for i in tip_info]
                    best_idx = np.argsort(dists)[-2:]
                    tip_a, child_a = tip_info[best_idx[0]]
                    tip_b, child_b = tip_info[best_idx[1]]
                    tip_a[0] += child_a.length or 0.0
                    tip_b[0] += child_b.length or 0.0
                n.MaxDistTips = [tip_a, tip_b]

    def _get_max_distance_singledesc(self):
        """returns the max distance between any pair of tips

        Also returns the tip names  that it is between as a tuple"""
        distmtx = self.tip_tip_distances()
        idx_max = divmod(distmtx.data.argmax(), distmtx.shape[1])
        max_pair = (distmtx.ids[idx_max[0]], distmtx.ids[idx_max[1]])
        return distmtx[idx_max], max_pair

    def get_max_distance(self):
        """Returns the max tip tip distance between any pair of tips

        Returns
        -------
        float
            The distance between the two most distant tips in the tree
        tuple of TreeNode
            The two most distant tips in the tree

        Raises
        ------
        NoLengthError
            A NoLengthError will be thrown if a node without length is
            encountered

        See Also
        --------
        distance
        tip_tip_distances
        compare_tip_distances

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a:1,b:2)c:3,(d:4,e:5)f:6)root;"))
        >>> dist, tips = tree.get_max_distance()
        >>> dist
        16.0
        >>> [n.name for n in tips]
        ['b', 'e']
        """
        if not hasattr(self, 'MaxDistTips'):
            # _set_max_distance will throw a TreeError if a node with a single
            # child is encountered
            try:
                self._set_max_distance()
            except TreeError:  #
                return self._get_max_distance_singledesc()

        longest = 0.0
        tips = [None, None]
        for n in self.non_tips(include_self=True):
            tip_a, tip_b = n.MaxDistTips
            dist = (tip_a[0] + tip_b[0])

            if dist > longest:
                longest = dist
                tips = [tip_a[1], tip_b[1]]
        return longest, tips

    def tip_tip_distances(self, endpoints=None):
        """Returns distance matrix between pairs of tips, and a tip order.

        By default, all pairwise distances are calculated in the tree. If
        `endpoints` are specified, then only the distances between those tips
        are computed.

        Parameters
        ----------
        endpoints : list of TreeNode or str, or None
            A list of TreeNode objects or names of TreeNode objects

        Returns
        -------
        DistanceMatrix
            The distance matrix

        Raises
        ------
        ValueError
            If any of the specified `endpoints` are not tips
        NoLengthError
            If a node without length is encountered

        See Also
        --------
        distance
        compare_tip_distances

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a:1,b:2)c:3,(d:4,e:5)f:6)root;"))
        >>> mat = tree.tip_tip_distances()
        >>> print(mat)
        4x4 distance matrix
        IDs:
        a, b, d, e
        Data:
        [[  0.   3.  14.  15.]
         [  3.   0.  15.  16.]
         [ 14.  15.   0.   9.]
         [ 15.  16.   9.   0.]]

        """
        all_tips = list(self.tips())
        if endpoints is None:
            tip_order = all_tips
        else:
            tip_order = [self.find(n) for n in endpoints]
            for n in tip_order:
                if not n.is_tip():
                    raise ValueError("Node with name '%s' is not a tip." %
                                     n.name)

        # linearize all tips in postorder
        # .__start, .__stop compose the slice in tip_order.
        for i, node in enumerate(all_tips):
            node.__start, node.__stop = i, i + 1

        # the result map provides index in the result matrix
        result_map = {n.__start: i for i, n in enumerate(tip_order)}
        num_all_tips = len(all_tips)  # total number of tips
        num_tips = len(tip_order)  # total number of tips in result
        result = np.zeros((num_tips, num_tips), float)  # tip by tip matrix
        distances = np.zeros((num_all_tips), float)  # dist from tip to tip

        def update_result():
            # set tip_tip distance between tips of different child
            for child1, child2 in combinations(node.children, 2):
                for tip1 in range(child1.__start, child1.__stop):
                    if tip1 not in result_map:
                        continue
                    t1idx = result_map[tip1]
                    for tip2 in range(child2.__start, child2.__stop):
                        if tip2 not in result_map:
                            continue
                        t2idx = result_map[tip2]
                        result[t1idx, t2idx] = distances[
                            tip1] + distances[tip2]

        for node in self.postorder():
            if not node.children:
                continue
            # subtree with solved child wedges
            # can possibly use np.zeros
            starts, stops = [], []  # to calc ._start and ._stop for curr node
            for child in node.children:
                if child.length is None:
                    raise NoLengthError("Node with name '%s' doesn't have a "
                                        "length." % child.name)

                distances[child.__start:child.__stop] += child.length

                starts.append(child.__start)
                stops.append(child.__stop)

            node.__start, node.__stop = min(starts), max(stops)

            if len(node.children) > 1:
                update_result()

        return DistanceMatrix(result + result.T, [n.name for n in tip_order])

    def compare_rfd(self, other, proportion=False):
        """Calculates the Robinson and Foulds symmetric difference

        Parameters
        ----------
        other : TreeNode
            A tree to compare against
        proportion : bool
            Return a proportional difference

        Returns
        -------
        float
            The distance between the trees

        Notes
        -----
        Implementation based off of code by Julia Goodrich. The original
        description of the algorithm can be found in [1]_.

        Raises
        ------
        ValueError
            If the tip names between `self` and `other` are equal.

        See Also
        --------
        compare_subsets
        compare_tip_distances

        References
        ----------
        .. [1] Comparison of phylogenetic trees. Robinson and Foulds.
           Mathematical Biosciences. 1981. 53:131-141

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree1 = TreeNode.read(StringIO("((a,b),(c,d));"))
        >>> tree2 = TreeNode.read(StringIO("(((a,b),c),d);"))
        >>> tree1.compare_rfd(tree2)
        2.0

        """
        t1names = {n.name for n in self.tips()}
        t2names = {n.name for n in other.tips()}

        if t1names != t2names:
            if t1names < t2names:
                tree1 = self
                tree2 = other.shear(t1names)
            else:
                tree1 = self.shear(t2names)
                tree2 = other
        else:
            tree1 = self
            tree2 = other

        tree1_sets = tree1.subsets()
        tree2_sets = tree2.subsets()

        not_in_both = tree1_sets.symmetric_difference(tree2_sets)

        dist = float(len(not_in_both))

        if proportion:
            total_subsets = len(tree1_sets) + len(tree2_sets)
            dist = dist / total_subsets

        return dist

    def compare_subsets(self, other, exclude_absent_taxa=False):
        """Returns fraction of overlapping subsets where self and other differ.

        Names present in only one of the two trees will count as mismatches,
        if you don't want this behavior, strip out the non-matching tips first.

        Parameters
        ----------
        other : TreeNode
            The tree to compare
        exclude_absent_taxa : bool
            Strip out names that don't occur in both trees

        Returns
        -------
        float
            The fraction of overlapping subsets that differ between the trees

        See Also
        --------
        compare_rfd
        compare_tip_distances
        subsets

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree1 = TreeNode.read(StringIO("((a,b),(c,d));"))
        >>> tree2 = TreeNode.read(StringIO("(((a,b),c),d);"))
        >>> tree1.compare_subsets(tree2)
        0.5

        """
        self_sets, other_sets = self.subsets(), other.subsets()

        if exclude_absent_taxa:
            in_both = self.subset() & other.subset()
            self_sets = (i & in_both for i in self_sets)
            self_sets = frozenset({i for i in self_sets if len(i) > 1})
            other_sets = (i & in_both for i in other_sets)
            other_sets = frozenset({i for i in other_sets if len(i) > 1})

        total_subsets = len(self_sets) + len(other_sets)
        intersection_length = len(self_sets & other_sets)

        if not total_subsets:  # no common subsets after filtering, so max dist
            return 1

        return 1 - (2 * intersection_length / float(total_subsets))

    def compare_tip_distances(self, other, sample=None, dist_f=distance_from_r,
                              shuffle_f=np.random.shuffle):
        """Compares self to other using tip-to-tip distance matrices.

        Value returned is `dist_f(m1, m2)` for the two matrices. Default is
        to use the Pearson correlation coefficient, with +1 giving a distance
        of 0 and -1 giving a distance of +1 (the maximum possible value).
        Depending on the application, you might instead want to use
        distance_from_r_squared, which counts correlations of both +1 and -1
        as identical (0 distance).

        Note: automatically strips out the names that don't match (this is
        necessary for this method because the distance between non-matching
        names and matching names is undefined in the tree where they don't
        match, and because we need to reorder the names in the two trees to
        match up the distance matrices).

        Parameters
        ----------
        other : TreeNode
            The tree to compare
        sample : int or None
            Randomly subsample the tips in common between the trees to
            compare. This is useful when comparing very large trees.
        dist_f : function
            The distance function used to compare two the tip-tip distance
            matrices
        shuffle_f : function
            The shuffling function used if `sample` is not None

        Returns
        -------
        float
            The distance between the trees

        Raises
        ------
        ValueError
            A ValueError is raised if there does not exist common tips
            between the trees

        See Also
        --------
        compare_subsets
        compare_rfd

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> # note, only three common taxa between the trees
        >>> tree1 = TreeNode.read(StringIO("((a:1,b:1):2,(c:0.5,X:0.7):3);"))
        >>> tree2 = TreeNode.read(StringIO("(((a:1,b:1,Y:1):2,c:3):1,Z:4);"))
        >>> dist = tree1.compare_tip_distances(tree2)
        >>> print("%.9f" % dist)
        0.000133446

        """
        self_names = {i.name: i for i in self.tips()}
        other_names = {i.name: i for i in other.tips()}
        common_names = frozenset(self_names) & frozenset(other_names)
        common_names = list(common_names)

        if not common_names:
            raise ValueError("No tip names in common between the two trees.")

        if len(common_names) <= 2:
            return 1  # the two trees must match by definition in this case

        if sample is not None:
            shuffle_f(common_names)
            common_names = common_names[:sample]

        self_nodes = [self_names[k] for k in common_names]
        other_nodes = [other_names[k] for k in common_names]

        self_matrix = self.tip_tip_distances(endpoints=self_nodes)
        other_matrix = other.tip_tip_distances(endpoints=other_nodes)

        return dist_f(self_matrix, other_matrix)

    def index_tree(self):
        """Index a tree for rapid lookups within a tree array

        Indexes nodes in-place as `n._leaf_index`.

        Returns
        -------
        dict
            A mapping {node_id: TreeNode}
        list of tuple of (int, int, int)
            The first index in each tuple is the corresponding node_id. The
            second index is the left most leaf index. The third index is the
            right most leaf index
        """
        self.assign_ids()

        id_index = {}
        child_index = []

        for n in self.postorder():
            for c in n.children:
                id_index[c.id] = c

                if c:
                    # c has children itself, so need to add to result
                    child_index.append((c.id,
                                        c.children[0].id,
                                        c.children[-1].id))

        # handle root, which should be t itself
        id_index[self.id] = self

        # only want to add to the child_index if self has children...
        if self.children:
            child_index.append((self.id,
                                self.children[0].id,
                                self.children[-1].id))

        return id_index, child_index

    def assign_ids(self):
        """Assign topologically stable unique ids to self

        Following the call, all nodes in the tree will have their id
        attribute set
        """
        curr_index = 0
        for n in self.postorder():
            for c in n.children:
                c.id = curr_index
                curr_index += 1

        self.id = curr_index

    def descending_branch_length(self, tip_subset=None):
        """Find total descending branch length from self or subset of self tips

        Parameters
        ----------
        tip_subset : Iterable, or None
            If None, the total descending branch length for all tips in the
            tree will be returned. If a list of tips is provided then only the
            total descending branch length associated with those tips will be
            returned.

        Returns
        -------
        float
            The total descending branch length for the specified set of tips.

        Raises
        ------
        ValueError
            A ValueError is raised if the list of tips supplied to tip_subset
            contains internal nodes or non-tips.

        Notes
        -----
        This function replicates cogent's totalDescendingBranch Length method
        and extends that method to allow the calculation of total descending
        branch length of a subset of the tips if requested. The postorder
        guarantees that the function will always be able to add the descending
        branch length if the node is not a tip.

        Nodes with no length will have their length set to 0. The root length
        (if it exists) is ignored.

        Examples
        --------
        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tr = TreeNode.read(StringIO("(((A:.1,B:1.2)C:.6,(D:.9,E:.6)F:.9)G"
        ...                             ":2.4,(H:.4,I:.5)J:1.3)K;"))
        >>> tdbl = tr.descending_branch_length()
        >>> sdbl = tr.descending_branch_length(['A','E'])
        >>> print(tdbl, sdbl)
        8.9 2.2
        """
        self.assign_ids()
        if tip_subset is not None:
            all_tips = self.subset()
            if not set(tip_subset).issubset(all_tips):
                raise ValueError('tip_subset contains ids that arent tip '
                                 'names.')

            lca = self.lowest_common_ancestor(tip_subset)
            ancestors = {}
            for tip in tip_subset:
                curr = self.find(tip)
                while curr is not lca:
                    ancestors[curr.id] = curr.length if curr.length is not \
                        None else 0.0
                    curr = curr.parent
            return sum(ancestors.values())

        else:
            return sum(n.length for n in self.postorder(include_self=True) if
                       n.length is not None)

    def cache_attr(self, func, cache_attrname, cache_type=list):
        """Cache attributes on internal nodes of the tree

        Parameters
        ----------
        func : function
            func will be provided the node currently being evaluated and must
            return a list of item (or items) to cache from that node or an
            empty list.
        cache_attrname : str
            Name of the attribute to decorate on containing the cached values
        cache_type : {set, frozenset, list}
            The type of the cache

        Notes
        -----
        This method is particularly useful if you need to frequently look up
        attributes that would normally require a traversal of the tree.

        WARNING: any cache created by this method will be invalidated if the
        topology of the tree changes (e.g., if `TreeNode.invalidate_caches` is
        called).

        Raises
        ------
        TypeError
            If an cache_type that is not a `set` or a `list` is specified.

        Examples
        --------
        Cache the tip names of the tree on its internal nodes

        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b,(c,d)e)f,(g,h)i)root;"))
        >>> f = lambda n: [n.name] if n.is_tip() else []
        >>> tree.cache_attr(f, 'tip_names')
        >>> for n in tree.traverse(include_self=True):
        ...     print("Node name: %s, cache: %r" % (n.name, n.tip_names))
        Node name: root, cache: ['a', 'b', 'c', 'd', 'g', 'h']
        Node name: f, cache: ['a', 'b', 'c', 'd']
        Node name: a, cache: ['a']
        Node name: b, cache: ['b']
        Node name: e, cache: ['c', 'd']
        Node name: c, cache: ['c']
        Node name: d, cache: ['d']
        Node name: i, cache: ['g', 'h']
        Node name: g, cache: ['g']
        Node name: h, cache: ['h']

        """
        if cache_type in [set, frozenset]:
            reduce_f = lambda a, b: a | b
        elif cache_type == list:
            reduce_f = lambda a, b: a + b
        else:
            raise TypeError("Only list, set and frozenset are supported!")

        for node in self.postorder(include_self=True):
            node._registered_caches.add(cache_attrname)

            cached = [getattr(c, cache_attrname) for c in node.children]
            cached.append(cache_type(func(node)))
            setattr(node, cache_attrname, reduce(reduce_f, cached))

    def shuffle(self, k=None, names=None, shuffle_f=np.random.shuffle, n=1):
        """Yield trees with shuffled tip names

        Parameters
        ----------
        k : int, optional
            The number of tips to shuffle. If k is not `None`, k tips are
            randomly selected, and only those names will be shuffled.
        names : list, optional
            The specific tip names to shuffle. k and names cannot be specified
            at the same time.
        shuffle_f : func
            Shuffle method, this function must accept a list and modify
            inplace.
        n : int, optional
            The number of iterations to perform. Value must be > 0 and `np.inf`
            can be specified for an infinite number of iterations.

        Notes
        -----
        Tip names are shuffled inplace. If neither `k` nor `names` are
        provided, all tips are shuffled.

        Returns
        -------
        GeneratorType
            Yielding TreeNode

        Raises
        ------
        ValueError
            If `k` is < 2
            If `n` is < 1
        ValueError
            If both `k` and `names` are specified
        MissingNodeError
            If `names` is specified but one of the names cannot be found

        Examples
        --------
        Alternate the names on two of the tips, 'a', and 'b', and do this 5
        times.

        >>> from six import StringIO
        >>> from skbio import TreeNode
        >>> tree = TreeNode.read(StringIO("((a,b),(c,d));"))
        >>> rev = lambda items: items.reverse()
        >>> shuffler = tree.shuffle(names=['a', 'b'], shuffle_f=rev, n=5)
        >>> for shuffled_tree in shuffler:
        ...     print(shuffled_tree)
        ((b,a),(c,d));
        <BLANKLINE>
        ((a,b),(c,d));
        <BLANKLINE>
        ((b,a),(c,d));
        <BLANKLINE>
        ((a,b),(c,d));
        <BLANKLINE>
        ((b,a),(c,d));
        <BLANKLINE>

        """
        if k is not None and k < 2:
            raise ValueError("k must be None or >= 2")
        if k is not None and names is not None:
            raise ValueError("n and names cannot be specified at the sametime")
        if n < 1:
            raise ValueError("n must be > 0")

        self.assign_ids()

        if names is None:
            all_tips = list(self.tips())

            if n is None:
                n = len(all_tips)

            shuffle_f(all_tips)
            names = [tip.name for tip in all_tips[:k]]

        nodes = [self.find(name) for name in names]

        # Since the names are being shuffled, the association between ID and
        # name is no longer reliable
        self.invalidate_caches()

        counter = 0
        while counter < n:
            shuffle_f(names)
            for node, name in zip(nodes, names):
                node.name = name

            yield self
            counter += 1


def _dnd_tokenizer(data):
    r"""Tokenizes data into a stream of punctuation, labels and lengths.

    Parameters
    ----------
    data : str
        a DND-like (e.g., newick) string

    Returns
    -------
    GeneratorType
        Yields successive DND tokens

    See Also
    --------
    TreeNode.from_newick
    TreeNode.to_newick

    Examples
    --------
    >>> from skbio.tree._tree import _dnd_tokenizer
    >>> for token in _dnd_tokenizer("((tip1, tip2)internal1)"):
    ...     print(token)
    (
    (
    tip1
    ,
    tip2
    )
    internal1
    )

    """
    dnd_tokens = set('(:),;')

    in_quotes = False
    saved = []
    sa = saved.append
    for d in data:
        if d == "'":
            in_quotes = not in_quotes
        if d in dnd_tokens and not in_quotes:
            curr = ''.join(saved).strip()
            if curr:
                yield curr
            yield d
            saved = []
            sa = saved.append
        else:
            sa(d)