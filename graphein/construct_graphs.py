"""Class for working with Protein Structure Graphs"""
# %%
# Graphein
# Author: Arian Jamasb <arian@jamasb.io>
# License: MIT
# Project Website: https://github.com/a-r-j/graphein
# Code Repository: https://github.com/a-r-j/graphein
import os
import glob
import re
import pandas as pd
import numpy as np
import dgl
import subprocess
import requests
import networkx as nx
import torch as torch
import torch.nn.functional as F
from torch_geometric.data import Data
from biopandas.pdb import PandasPdb
from Bio.PDB import *
from Bio.PDB.DSSP import residue_max_acc, dssp_dict_from_pdb_file
from Bio.PDB.Polypeptide import aa1, one_to_three
from dgllife.utils import (
    mol_to_bigraph,
    mol_to_complete_graph,
    mol_to_nearest_neighbor_graph,
)
from dgllife.utils import (
    BaseBondFeaturizer,
    BaseAtomFeaturizer,
    CanonicalAtomFeaturizer,
    CanonicalBondFeaturizer,
)
from rdkit.Chem import MolFromPDBFile
from sklearn.metrics import pairwise_distances
from sklearn import preprocessing
from sklearn.neighbors import kneighbors_graph
from scipy import spatial
from typing import Any, Dict, NamedTuple, List, Optional, Union

from graphein import utils


# Todo add SS featuriser for Mol Graph?
# Todo atom featuriser
# Todo create SS element-level graph
# Mol graph Nearest Neighbour
# Todo PretrainAtom/BondFeaturizer
# Todo DataScience Type Hinting


class ProteinGraph(object):
    def __init__(
        self,
        granularity: str,
        keep_hets: bool,
        insertions: bool,
        get_contacts_path: str,
        pdb_dir: str,
        contacts_dir: str,
        exclude_waters: bool = True,
        covalent_bonds: bool = True,
        include_ss: bool = True,
        include_ligand: bool = False,
        intramolecular_interactions: Optional[List[str]] = None,
        graph_constructor: Optional[str] = None,
        edge_distance_cutoff: Optional[float] = None,
        verbose: bool = True,
        deprotonate: bool = False,
        remove_string_labels: bool = False,
        long_interaction_threshold: Optional[int] = None,
        node_featuriser: Optional[
            Union[BaseAtomFeaturizer, CanonicalAtomFeaturizer, str]
        ] = None,
        edge_featuriser: Optional[
            Union[BaseBondFeaturizer, CanonicalBondFeaturizer, str]
        ] = None,
    ) -> None:
        """
        Initialise ProteinGraph Generator Class

        :param granularity: Specifies granularity of the graph construction. {'atom', 'CA', 'CB'}. CA = Alpha Carbon, CB = Beta Carbon
        :type granularity: str
        :param keep_hets: Keep heteroatoms present in the PDB file. Typically, these correspond to metal ions or modified residues (e.g. MSE)
        :type keep_hets: bool
        :param insertions: Keep atoms/residues with multiple insertion positions. Multiple insertions exist when the electron density is too vague to define a single insertion
        :type insertions: bool
        :param node_featuriser: DGL Node featuriser for atom-level graphs. Canonical Featurises recommended.
        :type node_featuriser: DGL Node Featuriser
        :param pdb_dir: Directory to PDB files. We will download .PDB files to this folder if you don't have an existing local copy of the requisite structure
        :type pdb_dir: str
        :param contacts_dir: Directory to GetContacts files
        :type contacts_dir: str
        :param exclude_waters: Specifies inclusion of water molecules. Not yet fully operational.
        :type exclude_waters: bool
        :param covalent_bonds: Specifies inclusion of covalent backbone. E.g. joins adjacent residues in the sequence
        :type covalent_bonds: bool
        :param include_ss: Specifies inclusion of secondary structure features computed by DSSP. Future warning: this will be changed in a subsequent update for managing feature selection.
        :type include_ss: bool
        :param include_ligand: Not yet implemented. Will specify option to include bound ligand(s) in the graph.
        :type include_ligand: bool
        :param intramolecular_interactions: List of allowable intramolecular interactions to include from GetContacts. ['sb', 'pc', 'ps', 'ts', 'vdw', 'hb', 'hbb', 'hbsb', 'hbbb', 'hbss', 'wb', 'wb2',
                                      'hblb', 'hbls', 'lwb', 'lwb2', 'hp']. See https://getcontacts.github.io/interactions.html for details.
        :type intramolecular_interactions: list
        :param edge_distance_cutoff: Distance in angstroms specifying cutoff distance for constructing an edge when using distance construction
        :type edge_distance_cutoff: float
        :param long_interaction_threshold: Specifies minimum distance in sequence for two nodes to be connected
        :type long_interaction_threshold: int
        """
        self.long_interaction_threshold = long_interaction_threshold
        self.remove_string_labels = remove_string_labels
        self.verbose = verbose
        self.edge_distance_cutoff = edge_distance_cutoff
        self.include_ligand = include_ligand
        self.include_ss = include_ss
        self.granularity = granularity
        self.keep_hets = keep_hets
        self.insertions = insertions
        self.node_featuriser = node_featuriser
        self.embedding_dict = {
            "meiler": {
                "ALA": [1.28, 0.05, 1.00, 0.31, 6.11, 0.42, 0.23],
                "GLY": [0.00, 0.00, 0.00, 0.00, 6.07, 0.13, 0.15],
                "VAL": [3.67, 0.14, 3.00, 1.22, 6.02, 0.27, 0.49],
                "LEU": [2.59, 0.19, 4.00, 1.70, 6.04, 0.39, 0.31],
                "ILE": [4.19, 0.19, 4.00, 1.80, 6.04, 0.30, 0.45],
                "PHE": [2.94, 0.29, 5.89, 1.79, 5.67, 0.30, 0.38],
                "TYR": [2.94, 0.30, 6.47, 0.96, 5.66, 0.25, 0.41],
                "PTR": [2.94, 0.30, 6.47, 0.96, 5.66, 0.25, 0.41],
                "TRP": [3.21, 0.41, 8.08, 2.25, 5.94, 0.32, 0.42],
                "THR": [3.03, 0.11, 2.60, 0.26, 5.60, 0.21, 0.36],
                "TPO": [3.03, 0.11, 2.60, 0.26, 5.60, 0.21, 0.36],
                "SER": [1.31, 0.06, 1.60, -0.04, 5.70, 0.20, 0.28],
                "SEP": [1.31, 0.06, 1.60, -0.04, 5.70, 0.20, 0.28],
                "ARG": [2.34, 0.29, 6.13, -1.01, 10.74, 0.36, 0.25],
                "LYS": [1.89, 0.22, 4.77, -0.99, 9.99, 0.32, 0.27],
                "KCX": [1.89, 0.22, 4.77, -0.99, 9.99, 0.32, 0.27],
                "LLP": [1.89, 0.22, 4.77, -0.99, 9.99, 0.32, 0.27],
                "HIS": [2.99, 0.23, 4.66, 0.13, 7.69, 0.27, 0.30],
                "ASP": [1.60, 0.11, 2.78, -0.77, 2.95, 0.25, 0.20],
                "GLU": [1.56, 0.15, 3.78, -0.64, 3.09, 0.42, 0.21],
                "PCA": [1.56, 0.15, 3.78, -0.64, 3.09, 0.42, 0.21],
                "ASN": [1.60, 0.13, 2.95, -0.60, 6.52, 0.21, 0.22],
                "GLN": [1.56, 0.18, 3.95, -0.22, 5.65, 0.36, 0.25],
                "MET": [2.35, 0.22, 4.43, 1.23, 5.71, 0.38, 0.32],
                "MSE": [2.35, 0.22, 4.43, 1.23, 5.71, 0.38, 0.32],
                "PRO": [2.67, 0.00, 2.72, 0.72, 6.80, 0.13, 0.34],
                "CYS": [1.77, 0.13, 2.43, 1.54, 6.35, 0.17, 0.41],
                "CSO": [1.77, 0.13, 2.43, 1.54, 6.35, 0.17, 0.41],
                "CAS": [1.77, 0.13, 2.43, 1.54, 6.35, 0.17, 0.41],
                "CAF": [1.77, 0.13, 2.43, 1.54, 6.35, 0.17, 0.41],
                "CSD": [1.77, 0.13, 2.43, 1.54, 6.35, 0.17, 0.41],
                "UNKNOWN": [0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
            },
            "kidera": {
                "A": [
                    -1.56,
                    -1.67,
                    -0.97,
                    -0.27,
                    -0.93,
                    -0.78,
                    -0.2,
                    -0.08,
                    0.21,
                    -0.48,
                ],
                "C": [0.12, -0.89, 0.45, -1.05, -0.71, 2.41, 1.52, -0.69, 1.13, 1.1],
                "E": [-1.45, 0.19, -1.61, 1.17, -1.31, 0.4, 0.04, 0.38, -0.35, -0.12],
                "D": [0.58, -0.22, -1.58, 0.81, -0.92, 0.15, -1.52, 0.47, 0.76, 0.7],
                "G": [1.46, -1.96, -0.23, -0.16, 0.1, -0.11, 1.32, 2.36, -1.66, 0.46],
                "F": [-0.21, 0.98, -0.36, -1.43, 0.22, -0.81, 0.67, 1.1, 1.71, -0.44],
                "I": [-0.73, -0.16, 1.79, -0.77, -0.54, 0.03, -0.83, 0.51, 0.66, -1.78],
                "H": [-0.41, 0.52, -0.28, 0.28, 1.61, 1.01, -1.85, 0.47, 1.13, 1.63],
                "K": [-0.34, 0.82, -0.23, 1.7, 1.54, -1.62, 1.15, -0.08, -0.48, 0.6],
                "M": [-1.4, 0.18, -0.42, -0.73, 2.0, 1.52, 0.26, 0.11, -1.27, 0.27],
                "L": [-1.04, 0.0, -0.24, -1.1, -0.55, -2.05, 0.96, -0.76, 0.45, 0.93],
                "N": [1.14, -0.07, -0.12, 0.81, 0.18, 0.37, -0.09, 1.23, 1.1, -1.73],
                "Q": [-0.47, 0.24, 0.07, 1.1, 1.1, 0.59, 0.84, -0.71, -0.03, -2.33],
                "P": [2.06, -0.33, -1.15, -0.75, 0.88, -0.45, 0.3, -2.3, 0.74, -0.28],
                "S": [
                    0.81,
                    -1.08,
                    0.16,
                    0.42,
                    -0.21,
                    -0.43,
                    -1.89,
                    -1.15,
                    -0.97,
                    -0.23,
                ],
                "R": [0.22, 1.27, 1.37, 1.87, -1.7, 0.46, 0.92, -0.39, 0.23, 0.93],
                "T": [0.26, -0.7, 1.21, 0.63, -0.1, 0.21, 0.24, -1.15, -0.56, 0.19],
                "W": [0.3, 2.1, -0.72, -1.57, -1.16, 0.57, -0.48, -0.4, -2.3, -0.6],
                "V": [-0.74, -0.71, 2.04, -0.4, 0.5, -0.81, -1.07, 0.06, -0.46, 0.65],
                "Y": [1.38, 1.48, 0.8, -0.56, -0.0, -0.68, -0.31, 1.03, -0.05, 0.53],
                "UNKNOWN": [0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
            },
        }
        self.pdb_dir = pdb_dir
        self.contacts_dir = contacts_dir
        self.get_contacts_path = get_contacts_path
        self.covalent_bonds = covalent_bonds
        self.deprotonate = deprotonate

        if not intramolecular_interactions:
            self.INTERACTION_TYPES = [
                "sb",
                "pc",
                "ps",
                "ts",
                "vdw",
                "hb",
                "hbb",
                "hbsb",
                "hbbb",
                "hbss",
                "wb",
                "wb2",
                "hblb",
                "hbls",
                "lwb",
                "lwb2",
                "hp",
            ]
        else:
            self.INTERACTION_TYPES = intramolecular_interactions
        self.INTERACTION_FDIM = len(self.INTERACTION_TYPES)

        # DGL Graph Constructors
        self.node_featuriser = node_featuriser
        self.edge_featuriser = edge_featuriser
        self.graph_constructor = graph_constructor

        if self.node_featuriser == "meiler":
            self.node_fdim = 7
        elif self.node_featuriser == "kidera":
            self.node_fdim = 10

        self.exclude_waters = exclude_waters

    def dgl_graph_from_pdb_code(
        self,
        pdb_code: Optional[str] = None,
        file_path: Optional[str] = None,
        chain_selection: str = "all",
        contact_file: Optional[str] = None,
        edge_construction: List[str] = ["contacts"],
        encoding: bool = False,
        k_nn: Optional[int] = None,
        custom_edges: Optional[pd.DataFrame] = None,
    ) -> dgl.DGLGraph:
        """
        Produces a DGL graph from a PDB code and a selection of polypeptide chains

        :param file_path:
        :type file_path: str
        :param custom_edges: Pass user-defined custom edges to use in edge construction, defaults to None
        :type custom_edges: Pandas DataFrame, optional
        :param edge_construction: Specifies edge construction methods. {'contact', 'distance', 'custom'}, defaults to ['contacts']
        :type edge_construction: list
        :param k_nn: Specifies number of nearest neighbours to make K_NN edges with
        :type k_nn: int
        :param encoding: Indicates whether or not node names and labels should be encoded
        :type encoding: bool
        :param contact_file: Path to local GetContacts output file, defaults to None
        :type contact_file: str
        :param pdb_code: 4 character PDB accession code
        :type pdb_code: str
        :param chain_selection: string indicating which chains to select {'A', 'B', 'AB', ..., 'all'}, defaults to 'all'
        :type chain_selection: str
        :return: DGLGraph object, nodes populated by residues or atoms as specified in class initialisation
        """
        # Todo Error Handling
        if pdb_code:
            assert not file_path, "Do not provide both a PDB file and a file path"
        if file_path:
            assert not pdb_code, "Do not provide both a PDB file and a file path"
        if k_nn:
            assert (
                "k_nn" in edge_construction
            ), "If providing KNN edges, include 'k_nn' in the edgge_construction list"

        if contact_file:
            assert (
                "contacts" not in edge_construction
            ), "do not provide a contacts file if not using contacts-based edge construction"

        if self.granularity == "atom":
            g = self._make_atom_graph(file_path)
            return g

        if self.granularity == "ss":
            raise NotImplementedError
            # todo SS granularity
            # get secondary structure nodes
            # add secondary stucture nodes
            # get secondary stucture edges
            # add secondary structure edges
            pass

        if self.granularity == "CA" or "CB" or "centroids":
            # Download PDB if file not found
            if pdb_code:
                pdb_path = self.pdb_dir + pdb_code + ".pdb"
                if not os.path.isfile(pdb_path):
                    self._download_pdb(pdb_code)

            # Create Relevant protein dataframes
            df = self._protein_df(pdb_path=self.pdb_dir + pdb_code + ".pdb")
            chains = self._get_chains(df, chain_selection)
            df = pd.concat(chains)
            # Populate graph with nodes
            g = self._add_protein_nodes(df)

            # Edge construction:
            if k_nn:
                edges = self._k_nn_edges(df, k_nn)
                g.add_edges(
                    edges["res1"],
                    edges["res2"],
                    data={"k_nn_dist": torch.Tensor(list(edges["distance"]))},
                )

            if not (contact_file and "contacts" in edge_construction):
                self._compute_protein_contacts(pdb_code)

            if "contacts" in edge_construction:
                edges = self._get_protein_edges(
                    pdb_code, chain_selection, contact_file=None
                )
                g = self._add_protein_edges_to_graph(g, edges)

            if self.edge_distance_cutoff and "distance" in edge_construction:
                # Get distance-based edges
                edges = self._distance_based_edges(df, self.edge_distance_cutoff)
                # Add edges
                g.add_edges(
                    list(edges["level_0"]),
                    list(edges["level_1"]),
                    data={"dist": torch.Tensor(list(edges[0]))},
                )

            if "delaunay" in edge_construction:
                edges = self._get_delaunay_edges(
                    df, furthest_site=False, incremental=False
                )
                g.add_edges(
                    list(edges["res1"]),
                    list(edges["res2"]),
                    data={
                        "delaunay_euclidean_distance": torch.Tensor(
                            list(edges["distance"])
                        )
                    },
                )

            # Add user supplied edges
            if custom_edges:
                g.add_edges(
                    list(custom_edges["res1"]),
                    list(custom_edges["res2"]),
                    data={"user_edge_data": torch.Tensor(list(custom_edges["data"]))},
                )

            if self.include_ss:
                dssp = self._get_protein_features(
                    pdb_code, file_path=None, chain_selection=chain_selection
                )
                feats = self._compute_protein_feature_representations(dssp)
                g = self._add_protein_features(g, feats)

        # Label Encoding of Node IDs
        if encoding:
            resiude_name_encoder = preprocessing.LabelEncoder()
            residue_id_encoder = preprocessing.LabelEncoder()

            residue_names = g.ndata["residue_name"]
            residue_id = g.ndata["id"]

            g.ndata["residue_name"] = resiude_name_encoder.fit_transform(residue_names)
            g.ndata["id"] = residue_id_encoder.fit_transform(residue_id)

            return g, resiude_name_encoder, residue_id_encoder

        if self.remove_string_labels:
            g.ndata.pop("residue_name")
            g.ndata.pop("id")

        if self.verbose:
            print(g)

        return g

    def dgl_graph_from_pdb_file(
        self,
        file_path: str,
        chain_selection: str,
        contact_file: Optional[str] = None,
        edges: Optional[pd.DataFrame] = None,
    ) -> dgl.DGLGraph:
        """
        Produces a DGL graph from a PDB file and a selection of polypeptide chains

        :param edges: User-defined custom edges, defaults to None
        :type edges: Pandas DataFrame, optional
        :param contact_file: Path to local GetContacts output file
        :type contact_file: str
        :param file_path: 4 character PDB accession code
        :type file_path: str
        :param chain_selection:  Polypeptide chains in structure to select {'A', 'B', 'AB', ..., 'all}
        :type chain_selection: str
        :return: DGLGraph object, nodes populated by residues or atoms as specified in class initialisation
        :rtype: DGLGraph
        """
        # Atom-level Graph
        if self.granularity == "atom":
            g = self._make_atom_graph(file_path)

        # Residue-level graph
        if self.granularity == "CA" or "CB":
            # Pre-process protein Df
            df = self._protein_df(pdb_path=file_path)
            chains = self._get_chains(df, chain_selection)
            df = pd.concat(chains)

            # Create Graph
            g = self._add_protein_nodes(df)

            # Add edges
            if not contact_file:
                self._compute_protein_contacts(file_path)
            if not edges:
                edges = self._get_protein_edges(
                    file_path, chain_selection, contact_file
                )
            g = self._add_protein_edges_to_graph(g, edges)

            if self.include_ss:

                dssp = self._get_protein_features(
                    file_path=file_path, pdb_code=None, chain_selection=chain_selection
                )

                feats = self._compute_protein_feature_representations(dssp)
                g = self._add_protein_features(g, feats)

        return g

    def nx_graph_from_pdb_code(
        self,
        pdb_code: str,
        chain_selection: str = "all",
        contact_file: Optional[str] = None,
        edge_construction: List[str] = ["contacts"],
        encoding: bool = False,
        k_nn: Optional[int] = None,
        custom_edges: Optional[pd.DataFrame] = None,
    ):
        """
        Produces a NetworkX Graph Object

        :param encoding:
        :type bool:
        :param edges: User-supplied edges, defaults to None
        :type edges: Pandas DataFrame, optional
        :param pdb_code: 4 character PDB accession code
        :type pdb_code: str
        :param chain_selection: string indicating chain selection {'A', 'B', 'AB', ..., 'all'}, defaults to 'all'
        :type chain_selection: str
        :param contact_file: Path to GetContacts output file.
        :type contact_file: str, optional
        :return: NetworkX graph object of protein
        :rtype: NetworkX graph
        """
        assert encoding, "Non-numeric feature encoding must be True"
        g, resiude_name_encoder, residue_id_encoder = self.dgl_graph_from_pdb_code(
            pdb_code=pdb_code,
            chain_selection=chain_selection,
            contact_file=contact_file,
            edge_construction=edge_construction,
            custom_edges=custom_edges,
            encoding=encoding,
            k_nn=k_nn,
        )
        node_attrs = g.node_attr_schemes().keys()
        edge_attrs = g.edge_attr_schemes().keys()

        return (
            dgl.to_networkx(g, node_attrs, edge_attrs),
            resiude_name_encoder,
            residue_id_encoder,
        )

    def nx_graph_from_pdb_file(
        self,
        pdb_code: str,
        chain_selection: str = "all",
        contact_file: Optional[str] = None,
    ):
        """
        Produces a NetworkX Graph Object

        :param pdb_code: 4 character PDB accession code
        :type pdb_code: str
        :param chain_selection: string indicating chain selection {'A', 'B', 'AB', ..., 'all'}
        :type chain_selection: str
        :param contact_file: Path to GetContacts output file.
        :type contact_file: str, optional
        :return: NetworkX graph object of protein
        """
        g, resiude_name_encoder, residue_id_encoder = self.dgl_graph_from_pdb_file(
            pdb_code, chain_selection, contact_file
        )
        node_attrs = g.node_attr_schemes().keys()
        edge_attrs = g.edge_attr_schemes().keys()
        return (
            dgl.to_networkx(g, node_attrs, edge_attrs),
            resiude_name_encoder,
            residue_id_encoder,
        )

    def torch_geometric_graph_from_pdb_code(
        self,
        pdb_code: str,
        chain_selection: str = "all",
        edge_construction: List[str] = ["contacts"],
        contact_file: Optional[str] = None,
        encoding: bool = False,
        k_nn: Optional[int] = None,
        custom_edges: Optional[pd.DataFrame] = None,
    ) -> Data:
        """
        Produces a PyToch Geometric Data object from a protein structure

        :param k_nn: Specifies  K nearest neighbours to use in KNN edge construction, defaults to None
        :type k_nn: int, optional
        :param custom_edges: User-supplied edges to use, defaults to None
        :type custom_edges: Pandas DataFrame, optional
        :param encoding:
        :type encoding: bool
        :param edge_construction: List containing edge construction to be used. ['contacts', 'distance', 'delaunay'], defaults to ['contacts']
        :type edge_construction: list
        :param pdb_code: 4-character PDB accession code
        :type pdb_code: str
        :param chain_selection: Specifies polypeptide chains to include. e.g. one of {'A', 'B' ,'AB', 'BC'}, defaults to 'all'
        :type chain_selection: str
        :param contact_file: Path to contact file if using local file.
        :type contact_file: str
        :return: Pytorch Geometric Graph of protein structure.
        :rtype: PyTorch Geometric Data object
        """
        assert encoding, "Non-numeric feature encoding must be True"

        g, resiude_name_encoder, residue_id_encoder = self.dgl_graph_from_pdb_code(
            pdb_code=pdb_code,
            chain_selection=chain_selection,
            contact_file=contact_file,
            edge_construction=edge_construction,
            custom_edges=custom_edges,
            encoding=encoding,
            k_nn=k_nn,
        )
        # Get node features from DGL graph and concatenate them
        node_feature_names = g.node_attr_schemes().keys()
        dgl_graph_features = [g.ndata[feat].float() for feat in node_feature_names]
        dgl_graph_features = [
            f.unsqueeze(dim=1) if len(f.shape) == 1 else f for f in dgl_graph_features
        ]
        node_features = torch.cat(dgl_graph_features, dim=1)

        # Get edge features from DGL graph and concatenate them
        edge_types = g.edge_attr_schemes().keys()
        edge_feats = [g.edata[e].float() for e in edge_types]
        edge_feats = [
            e.unsqueeze(dim=1) if len(e.shape) == 1 else e for e in edge_feats
        ]
        edge_feats = torch.cat(edge_feats, dim=1)

        # Create the Torch Geometric graph
        geom_graph = Data(
            x=node_features,
            edge_index=torch.stack(g.edges(), dim=1),
            edge_attr=edge_feats,
        )
        print(geom_graph)
        return geom_graph

    def _make_atom_graph(
        self,
        pdb_code: str = None,
        pdb_path: Optional[str] = None,
        node_featurizer: Optional[
            Union[BaseAtomFeaturizer, CanonicalAtomFeaturizer, str]
        ] = None,
        edge_featurizer: Optional[
            Union[BaseBondFeaturizer, CanonicalBondFeaturizer, str]
        ] = None,
        graph_type: str = "bigraph",
    ) -> dgl.DGLGraph:
        """
        Create atom-level graph from PDB structure

        :param graph_type:
        :param pdb_code:
        :param pdb_path:
        :param node_featurizer:
        :param edge_featurizer:
        :return:
        """

        if node_featurizer is None:
            node_featurizer = CanonicalAtomFeaturizer()
        if edge_featurizer is None:
            edge_featurizer = CanonicalBondFeaturizer()

        # Read in protein as mol
        # if pdb_path:
        if pdb_code:
            pdb_path = self.pdb_dir + pdb_code + ".pdb"
            if not os.path.isfile(pdb_path):
                self._download_pdb(pdb_code)

        assert os.path.isfile(pdb_path)
        mol = MolFromPDBFile(pdb_path)

        # DGL mol to graph
        if graph_type == "bigraph":
            g = mol_to_bigraph(
                mol, node_featurizer=node_featurizer, edge_featurizer=edge_featurizer
            )
        elif graph_type == "complete":
            g = mol_to_complete_graph(
                mol,
                node_featurizer=node_featurizer,
            )
        elif graph_type == "k_nn":
            raise NotImplementedError
        print(g)
        return g

    def _protein_df(self, pdb_path: str) -> pd.DataFrame:
        """
        Pre-processes protein structure dataframe.

        :param pdb_path:
        :param pdb_code - 4 letter PDB accession code
        :return: 'cleaned protein dataframe'
        """
        protein_df = PandasPdb().read_pdb(pdb_path)

        atoms = protein_df.df["ATOM"]
        hetatms = protein_df.df["HETATM"]

        if self.granularity == "centroids":
            if self.deprotonate:
                atoms = atoms.loc[atoms["atom_name"] != "H"].reset_index()
            centroids = self._calculate_centroid_positions(atoms)
            atoms = atoms.loc[atoms["atom_name"] == "CA"].reset_index()
            atoms["x_coord"] = centroids["x_coord"]
            atoms["y_coord"] = centroids["y_coord"]
            atoms["z_coord"] = centroids["z_coord"]
        else:
            atoms = atoms.loc[atoms["atom_name"] == self.granularity]

        if self.keep_hets:
            if self.exclude_waters:
                hetatms = hetatms.loc[hetatms["residue_name"] != "HOH"]
            if self.verbose:
                print(f"Detected {len(hetatms)} HETATOM nodes")
            protein_df = pd.concat([atoms, hetatms])
        else:
            protein_df = atoms

        # Remove alt_loc resdiues
        protein_df = protein_df.loc[protein_df["alt_loc"].isin(["", "A"])]

        if self.verbose:
            print(f"Detected {len(protein_df)} total nodes")
        return protein_df

    def _calculate_centroid_positions(self, atoms: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates position of sidechain centroids
        :param atoms: ATOM df of protein structure
        :return: centroids (df)
        """
        centroids = (
            atoms.groupby("residue_number")
            .mean()[["x_coord", "y_coord", "z_coord"]]
            .reset_index()
        )
        if self.verbose:
            print(f"Calculated {len(centroids)} centroid nodes")
        return centroids

    @staticmethod
    def _get_chains(
        protein_df: pd.DataFrame, chain_selection: List[str]
    ) -> List[pd.DataFrame]:
        """
        Extracts relevant chains from protein_df

        :param protein_df: pandas dataframe of PDB subsetted to relevant atoms (CA, CB)
        :param chain_selection:
        :return
        """
        if chain_selection != "all":
            chains = [
                protein_df.loc[protein_df["chain_id"] == chain]
                for chain in chain_selection
            ]
        else:
            chains = [
                protein_df.loc[protein_df["chain_id"] == chain]
                for chain in protein_df["chain_id"].unique()
            ]
        return chains

    def _add_protein_nodes(self, chain: List[pd.DataFrame]) -> dgl.DGLGraph:
        """
        Add protein nodes to graph from list of PandasPDB dataframes for each chain
        :param chain: (list of dataframes) Contains a dataframe for each chain in the protein
        :return: g (DGLGraph): Graph of protein only populated by the nodes
        """
        g = dgl.DGLGraph()

        nodes = (
            chain["chain_id"]
            + ":"
            + chain["residue_name"]
            + ":"
            + chain["residue_number"].apply(str)
        )
        if self.granularity == "atom":
            nodes = nodes + ":" + chain["atom_name"]
        node_features = [
            self._aa_features(residue, self.node_featuriser)
            for residue in chain["residue_name"]
        ]
        coords = torch.Tensor(
            np.asarray(chain[["x_coord", "y_coord", "z_coord"]])
        ).type("torch.FloatTensor")

        g.add_nodes(
            len(nodes),
            {
                "id": nodes,
                "residue_name": chain["residue_name"],
                "h": torch.stack(node_features).type("torch.FloatTensor"),
                "coords": coords,
            },
        )
        return g

    def _aa_features(self, residue: str, embedding: str = "meiler") -> torch.Tensor:
        """
        Retrieves amino acid embeddings
        :param residue: str specifying the amino acid
        :param embedding: embedding to use {'meiler', 'kidera'}
        :return: features: torch tensor of features
        """
        if residue not in self.embedding_dict[embedding].keys():
            residue = "UNKNOWN"
        features = torch.Tensor(self.embedding_dict[embedding][residue]).double()
        return features

    def _download_pdb(self, pdb_code: str) -> None:
        """
        Download PDB structure from PDB

        :param pdb_code: 4 character PDB accession code
        :return: # todo impl return
        """
        # Initialise class and download pdb file
        pdbl = PDBList()
        pdbl.retrieve_pdb_file(
            pdb_code, pdir=self.pdb_dir, overwrite=True, file_format="pdb"
        )
        # Rename file to .pdb from .ent
        os.rename(
            self.pdb_dir + "pdb" + pdb_code + ".ent", self.pdb_dir + pdb_code + ".pdb"
        )
        # Assert file has been downloaded
        assert any(pdb_code in s for s in os.listdir(self.pdb_dir))
        print(f"Downloaded PDB file for: {pdb_code}")

    def _compute_protein_contacts(
        self, pdb_code: str, file_name: Optional[str] = None
    ) -> None:
        """Computes contacts from .pdb file using GetContacts - https://www.github.com/getcontacts/getcontacs

        :param: pdb_code - 4 character PDB accession code
        :param file_name: search for GetContacts output file name
        """
        # Check for existence of contacts file
        if file_name is not None:
            contacts_file = glob.glob(self.contacts_dir + file_name)
        else:
            contacts_file = glob.glob(self.contacts_dir + "*" + pdb_code + "*.tsv")
        if contacts_file:
            print(f"Contact file found: {contacts_file}")
            return
        print(pdb_code)
        # Check for existence of pdb file
        pdb_file = glob.glob(self.pdb_dir + "*" + pdb_code + "*.pdb")
        print(pdb_file)
        if not pdb_file:
            # Download PDB file
            print("PDB file not downloaded")
            # self.download_pdb(pdb_code)
            pdb_file = self.pdb_dir + pdb_code + ".pdb"
        else:
            pdb_file = pdb_file[0]
            print(f"PDB file detected: {pdb_file}")

        # Run GetContacts
        command = f"{self.get_contacts_path}/get_static_contacts.py "
        command += f"--structure {pdb_file} "
        command += f'--output {self.contacts_dir + pdb_code + "_contacts.tsv"} '
        command += "--itypes all"  # --sele "protein"'
        subprocess.run(command, shell=True)
        assert os.path.isfile(self.contacts_dir + pdb_code + "_contacts.tsv")
        print(f"Computed Contacts for: {pdb_code}")

    def _get_protein_edges(
        self, pdb_code: str, chain_selection: str, contact_file: str
    ) -> pd.DataFrame:
        """
        Compute protein edges

        :param contact_file:
        :param chain_selection:
        :param pdb_code: 4 character pdb accession code
        :return: edges : dataframe containing edges derived from GetContacts analysis
        # todo impl covalent bond structure
        """
        if not contact_file:
            contact_file = self.contacts_dir + pdb_code + "_contacts" + ".tsv"
        edges = set()
        # Read Contacts File
        with open(contact_file, "r") as f:
            next(f)
            next(f)
            for line in f:
                linfo = line.strip().split("\t")
                interaction_type = linfo[1]
                # Select interacting Residues
                if self.granularity == "CA" or "CB" or "atom":
                    res1 = linfo[2]
                    res2 = linfo[3]
                    if self.granularity != "atom":
                        res1 = re.search(r".\:(.*?)\:(.*?)(?=:)", res1)[0]
                        res2 = re.search(r".\:(.*?)\:(.*?)(?=:)", res2)[0]
                # Add edge to set of edges
                edges.add((res1, res2, interaction_type))

        edges = pd.DataFrame(list(edges), columns=["res1", "res2", "interaction_type"])
        # Remove all unallowed interactions
        edges = edges.loc[edges["interaction_type"].isin(self.INTERACTION_TYPES)]

        if chain_selection != "all":
            edges = edges.loc[edges["res1"].str.startswith(tuple(chain_selection))]
            edges = edges.loc[edges["res2"].str.startswith(tuple(chain_selection))]

        # Filter out interactions for disordered/unassigned residues
        edges = edges.loc[~edges["res1"].str.contains("[A-Z]$")]
        edges = edges.loc[~edges["res2"].str.contains("[A-Z]$")]
        edges = edges.loc[~edges["res1"].str.contains(":0$")]
        edges = edges.loc[~edges["res2"].str.contains(":0$")]
        edges = edges.loc[~edges["res1"].str.contains("^X:")]
        edges = edges.loc[~edges["res2"].str.contains("^X:")]

        if self.long_interaction_threshold:
            res1 = edges["res1"].str.extract("(\d+)").astype(int)
            res2 = edges["res2"].str.extract("(\d+)").astype(int)
            inds = abs(res1 - res2) > self.long_interaction_threshold
            edges = edges[inds]

        if self.verbose:
            print(f"Calculated {len(edges)} intramolecular interaction-based edges")

        return edges

    def _add_protein_edges_to_graph(
        self, g: dgl.DGLGraph, e: pd.DataFrame
    ) -> dgl.DGLGraph:
        """
        Add protein edges from dataframe of edges

        :param g: Dgl graph of protein
        :type g: dgl.DGLGraph
        :param e: Pandas dataframe of edges
        :type e: pd.DataFrame
        :return: g DGL Graph with edges added
        """
        if self.granularity == "dense":
            g.add_edges(
                [
                    i
                    for i in range(g.number_of_nodes())
                    for j in range(g.number_of_nodes() - 1)
                ],
                [
                    j
                    for i in range(g.number_of_nodes())
                    for j in range(g.number_of_nodes())
                    if i != j
                ],
            )
            return g
        else:
            index = dict(zip(list(g.ndata["id"]), list(range(len(g.ndata["id"])))))

            # Remove interactions for edges between nodes not in graph. E.g hetatms
            e = e.loc[e["res1"].isin(index.keys())]
            e = e.loc[e["res2"].isin(index.keys())]

            res1_ind = [index[res] for res in e["res1"]]
            res2_ind = [index[res] for res in e["res2"]]
            interactions = [
                self._onek_encoding_unk(interaction, self.INTERACTION_TYPES)
                for interaction in e["interaction_type"]
            ]

            g.add_edges(
                res1_ind,
                res2_ind,
                {
                    "rel_type": torch.Tensor(interactions).double(),
                    "norm": torch.ones(len(interactions)),
                },
            )
            return g

    @staticmethod
    def _onek_encoding_unk(x: Any, allowable_set: List[Any]) -> List[bool]:
        """
        Function for one hot encoding
        :param x: value to one-hot
        :param allowable_set: set of options to encode
        :return: one-hot encoding as torch tensor
        """
        # if x not in allowable_set:
        #    x = allowable_set[-1]
        return [x == s for s in allowable_set]

    def _get_protein_features(
        self, pdb_code: Optional[str], file_path: Optional[str], chain_selection: str
    ) -> pd.DataFrame:
        """
        :param file_path: (str) file path to PDB file
        :param pdb_code: (str) String containing four letter PDB accession
        :return df (pd.DataFrame): Dataframe containing output of DSSP (Solvent accessibility, secondary structure for each residue)
        """

        # Run DSSP on relevant PDB file
        if pdb_code:
            d = dssp_dict_from_pdb_file(self.pdb_dir + pdb_code + ".pdb")
        if file_path:
            d = dssp_dict_from_pdb_file(file_path)

        # Parse DSSP output to DataFrame
        appender = []
        for k in d[1]:
            to_append = []
            y = d[0][k]
            chain = k[0]
            residue = k[1]
            het = residue[0]
            resnum = residue[1]
            icode = residue[2]
            to_append.extend([chain, resnum, icode])
            to_append.extend(y)
            appender.append(to_append)

        cols = [
            "chain",
            "resnum",
            "icode",
            "aa",
            "ss",
            "exposure_rsa",
            "phi",
            "psi",
            "dssp_index",
            "NH_O_1_relidx",
            "NH_O_1_energy",
            "O_NH_1_relidx",
            "O_NH_1_energy",
            "NH_O_2_relidx",
            "NH_O_2_energy",
            "O_NH_2_relidx",
            "O_NH_2_energy",
        ]

        df = pd.DataFrame.from_records(appender, columns=cols)
        # Subset dataframe to those in chain_selection
        if chain_selection != "all":
            df = df.loc[df["chain"].isin(chain_selection)]
        # Rename cysteines to 'C'
        df["aa"] = df["aa"].str.replace("[a-z]", "C")
        df = df[df["aa"].isin(list(aa1))]

        # Drop alt_loc residues
        df = df.loc[df["icode"] == " "]

        # Add additional Columns
        df["aa_three"] = df["aa"].apply(one_to_three)
        df["max_acc"] = df["aa_three"].map(residue_max_acc["Sander"].get)
        df[["exposure_rsa", "max_acc"]] = df[["exposure_rsa", "max_acc"]].astype(float)
        df["exposure_asa"] = df["exposure_rsa"] * df["max_acc"]
        df["index"] = df["chain"] + ":" + df["aa_three"] + ":" + df["resnum"].apply(str)
        return df

    def _compute_protein_feature_representations(
        self, dssp_df: pd.DataFrame
    ) -> Dict[str, torch.Tensor]:
        """
        :param dssp_df: (pd.DataFrame): Df containing parsed output of DSSP
        :return feature_dict (dict): Dictionary of tensorized features
        """
        # One hot encoded secondary structure assignments
        ss_set = ["G", "H", "I", "E", "B", "T", "S", "C", "-"]
        ss = [self._onek_encoding_unk(ss, ss_set) for ss in dssp_df["ss"]]
        # Create feature dictionary

        feature_dict = {
            "ss": torch.Tensor(ss),
            "asa": torch.Tensor(np.asarray(dssp_df["exposure_asa"])).reshape(
                len(dssp_df), 1
            ),
            "rsa": torch.Tensor(np.asarray(dssp_df["exposure_rsa"])).reshape(
                len(dssp_df), 1
            ),
        }
        return feature_dict

    @staticmethod
    def _add_protein_features(
        g: dgl.DGLGraph, feature_dict: Dict[str, torch.Tensor]
    ) -> dgl.DGLGraph:
        """
        Add computed protein features to graph


        :param g: DGL Graph of protein.
        :param feature_dict: Dictionary of features calculated by DSSP
        :return: g DGL Graph of protein with SS and solvent accessibility features added to node data
        """
        # 0 Pad Tensors for Proteins with HETATMS that DSSP Can't Deal with
        pad_length = len(g.ndata["h"]) - len(feature_dict["ss"])
        if pad_length > 0:
            pad = [0, 0, 0, pad_length]
            feature_dict["ss"] = F.pad(feature_dict["ss"], pad, "constant", 0)
            feature_dict["asa"] = F.pad(feature_dict["asa"], pad, "constant", 0)
            feature_dict["rsa"] = F.pad(feature_dict["rsa"], pad, "constant", 0)
        # Assign Features
        g.ndata["ss"] = feature_dict["ss"]
        g.ndata["asa"] = feature_dict["asa"]
        g.ndata["rsa"] = feature_dict["rsa"]
        return g

    def _k_nn_edges(
        self,
        protein_df: pd.DataFrame,
        k: int,
        mode: str = "connectivity",
        metric: str = "minkowski",
        p: int = 2,
        include_self: bool = False,
    ) -> pd.DataFrame:
        """
        Construct edges based on K nearest neighbours

        :param protein_df: PandasPDB DF of protein structure
        :param k: number of nearest neighbour edges for each node
        :param mode: {'connectivity', 'distance'}
        :param metric: {'minkowskii}
        :param p:
        :param include_self: bool - whether or not to include self-loops
        :return:
        """
        # Create distance matrix
        coords = protein_df[["x_coord", "y_coord", "z_coord"]]
        # dists = pairwise_distances(np.asarray(coords))
        dists = np.asarray(coords)
        # Perform K-NN on coordinates
        nn = kneighbors_graph(
            X=dists,
            n_neighbors=k,
            mode=mode,
            metric=metric,
            p=p,
            include_self=include_self,
        )
        # Create dataframe of edges
        outgoing = np.repeat(np.array(range(len(coords))), k)
        incoming = nn.indices
        edge_df = pd.DataFrame(
            {"res1": outgoing, "res2": incoming, "distance": nn.data}
        )

        if self.long_interaction_threshold:
            edge_df = edge_df.loc[
                abs(abs(edge_df["res1"]) - abs(edge_df["res2"]))
                > self.long_interaction_threshold
            ]

        if self.verbose:
            print(f"Calculated {len(edge_df)} K-nearest neighbour edges")
        return edge_df

    def _distance_based_edges(
        self, protein_df: pd.DataFrame, cutoff: float
    ) -> pd.DataFrame:
        """
        Calculate distance-based edges from coordinates in 3D structure.

        Produce Edge list dataframe based on pairwise distance matrix calculation
        :param protein_df: PandasPDB Dataframe
        :param cutoff: Distance threshold to create an edge (Angstroms)
        :return: dists : pandas dataframe of edge list and distance
        """
        # Create distance matrix
        coords = protein_df[["x_coord", "y_coord", "z_coord"]]
        dists = pairwise_distances(np.asarray(coords))
        # Filter distance matrix and select lower triangle
        dists = pd.DataFrame(np.tril(np.where(dists < cutoff, dists, 0)))
        # Reshape to produce edge list
        dists.values[[np.arange(len(dists))] * 2] = np.nan
        dists = dists.stack().reset_index()
        # Filter to remove edges that exceed cutoff
        dists = dists.loc[dists[0] != 0]

        if self.long_interaction_threshold:
            dists = dists.loc[
                abs(abs(dists["level_0"]) - abs(dists["level_1"]))
                > self.long_interaction_threshold
            ]

        if self.verbose:
            print(f"Calcuclated {len(dists)} distance-based edges")
        return dists

    """"
    @staticmethod
    def get_voronoi_edges(protein_df, furthest_site=False, incremental=False):
        
        #Calculate Voronoi edges from protein dataframe
        #:param protein_df:
        #:param furthest_site:
        ##:param incremental:
        #:return:
        
        coord = protein_df[['x_coord', 'y_coord', 'z_coord']]
        vor = spatial.Voronoi(points=coord, furthest_site=furthest_site, incremental=incremental)
        edges = pd.DataFrame(vor.ridge_points)
        print(edges)
        edges.columns = ['res1', 'res2']
        print(f'Calculated {len(edges)} voronoi-ridge edges')
        return edges
    """

    def _get_delaunay_edges(
        self,
        protein_df: pd.DataFrame,
        furthest_site: bool = False,
        incremental: bool = False,
    ) -> pd.DataFrame:
        """
        Calculate Delaunay edges from a dataframe of coordinates
        :param protein_df:
        :param furthest_site:
        :param incremental:
        :return:
        """
        coord = protein_df[["x_coord", "y_coord", "z_coord"]]
        delaunay = spatial.Delaunay(
            coord, furthest_site=furthest_site, incremental=incremental
        )

        # Turn simplices into edgelist
        edges = []
        indices, indptr = delaunay.vertex_neighbor_vertices
        for i in range(indices.shape[0] - 1):
            for j in indptr[indices[i] : indices[i + 1]]:
                try:
                    edges.append([i, j])
                except IndexError:
                    pass

        # Create edge DataFrame
        edge_df = pd.DataFrame(edges)
        edge_df.columns = ["res1", "res2"]

        # Get distances between edges
        distances = []
        for row in range(len(edge_df)):
            a = coord.iloc[edge_df.iloc[row]["res1"]]
            b = coord.iloc[edge_df.iloc[row]["res2"]]
            distances.append(spatial.distance.euclidean(a, b))

        edge_df["distance"] = distances

        if self.long_interaction_threshold:
            edge_df = edge_df.loc[
                abs(abs(edge_df["res1"]) - abs(edge_df["res2"]))
                > self.long_interaction_threshold
            ]

        if self.verbose:
            print(f"Calculated {len(edge_df)} Delaunay edges")
        return edge_df


class RNAGraph:
    def __init__(self, verbose: bool = True):
        """
        This class handles graph construction from RNA structures

        :param verbose: Specifies whether or not to print a summary of the graph constructed.
        :type verbose: bool, optional
        """
        self.verbose = verbose
        self.RNA_bases = ["A", "U", "G", "C", "I"]

    def dgl_graph_from_dotbracket(
        self, dotbracket: str, sequence: Optional[str] = None
    ) -> dgl.DGLGraph:
        """
        This function builds a DGL Graph from dotbracket notation of RNA secondary structure.

        :param dotbracket: RNA Structure in dotbracket notation
        :type dotbracket: str, required
        :param sequence: RNA Sequence. If provided, it is used to featurise nodes if
        :type sequence: str, optional
        :return: DGLGraph
        """

        # Todo: pairing in pseudoknots

        # Initialise graph with number of nodes
        g = dgl.DGLGraph()
        g.add_nodes(len(dotbracket))

        # Add encoding of bases, if a sequence is provided
        if sequence:
            assert len(sequence) == len(
                dotbracket
            ), "Sequence and dotbracket lengths must match"
            features = []
            for c in sequence:
                features.append(
                    torch.Tensor(utils.onek_encoding_unk(c, self.RNA_bases))
                )
            features = torch.stack(features, dim=0)
            g.ndata["x"] = features

        # Iterate over dotbracket to build connectivity
        bases = []
        for i, c in enumerate(dotbracket):
            # Add adjacent edges
            if i > 0:
                g.add_edge(i, i - 1, {"rel_type": torch.Tensor(0)})
            if c == "(":
                bases.append(i)
            elif c == ")":
                neighbor = bases.pop()
                g.add_edge(i, neighbor, {"rel_type": torch.Tensor(1)})
            elif c == ".":
                continue
            else:
                print("Input is not in dot-bracket notation!")
                return None
        if self.verbose:
            print(g)
        return g


class PPIGraph:
    def __init__(
            self,
            protein_list: List[str],
            sources: Optional[Union[List[str], None]],
            verbose: bool = True,
            paginate: bool = True,
            **kwargs
    ) -> None:
        """
        Initialise PPIGraph.
        See also [1] STRING: https://string-db.org/help/api
                 [2] BIOGRID: https://wiki.thebiogrid.org/doku.php/biogridrest

        :param protein_list: Proteins to include in the graph
        :param sources: List of sources (databases) to retrieve the data from.
                        By default it includes interactions from all sources (STRING, BIOGRID)
        :param paginate: Whether to paginate the API calls for the sources that require it. Default is True
        :param kwargs: Parameters of the API calls, used to select and filter the results. The parameter names
                       are of the form <SOURCE>_<param>, where <SOURCE> is the database name and <param> is
                       the name of the parameter. Information about these parameters is documented at the websites
                       of the API REST providers (e.g. [1] and [2])
        """
        self.protein_list = np.unique(protein_list)
        self.verbose = verbose
        self.ncbi_taxon_id = 9606  # 9606 corresponds to humans. TODO: allow other organisms?
        self.paginate = paginate
        self.kwargs = kwargs

        # Check sources
        self.valid_sources = ["STRING", "BIOGRID"]
        self.sources = self.valid_sources
        if sources is not None:
            self.sources = np.unique(sources)
            self._validate_sources()

    def _validate_sources(
            self
    ) -> None:
        """
        Ensures that all the input sources are valid
        """
        for s in self.sources:
            if s.upper() not in self.valid_sources:
                raise ValueError("Source '{}' is not supported".format(s))

    @staticmethod
    def _params_STRING(
            params: Dict[str, Union[str, int, List[str], List[int]]],
            **kwargs
    ) -> Dict[str, Union[str, int]]:
        """
        Updates default parameters with user parameters for the method "network" of the STRING API REST.
        See also https://string-db.org/help/api/
        :param params: Dictionary of default parameters
        :param kwargs: User parameters for the method "network" of the STRING API REST. The key must start with "STRING"
        :return: Dictionary of parameters
        """
        # TODO: Might be possible to generalise this function for all sources
        fields = ["species",  # NCBI taxon identifiers
                  "required_score",  # threshold of significance to include a interaction, a number between 0 and 1000
                  # (default depends on the network)
                  "network_type",  # network type: functional (default), physical
                  "add_nodes",  # adds a number of proteins to the network based on their confidence score,
                  # e.g., extends the interaction neighborhood of selected proteins to desired value
                  "show_query_node_labels"  # when available use submitted names in the preferredName column when
                  # (0 or 1) (default:0)
                  ]
        for p in fields:
            kwarg_name = "STRING_" + p
            if kwarg_name in kwargs:
                value = kwargs[kwarg_name]
                if type(value) is list:
                    value = "%0d".join(value)
                params[p] = value
        return params

    @staticmethod
    def _parse_STRING(
            protein_list: List[str],
            ncbi_taxon_id: Union[int, str, List[int], List[str]],
            **kwargs
    ) -> pd.DataFrame:
        """
        Makes STRING API call and returns a source specific Pandas dataframe.
        See also [1] STRING: https://string-db.org/help/api/
        :param protein_list: Proteins to include in the graph
        :param ncbi_taxon_id: NCBI taxonomy identifiers for the organism. Default is 9606 (Homo Sapiens)
        :param kwargs: Parameters of the "network" method of the STRING API REST, used to select the results. The
                       parameter names are of the form STRING_<param>, where <param> is the name of the parameter.
                       Information about these parameters can be found at [1].
        :return: Source specific Pandas dataframe.
        """
        # Prepare call to STRING API
        string_api_url = "https://string-db.org/api"
        output_format = "json"  # "tsv-no-header"
        method = "network"
        request_url = "/".join([string_api_url, output_format, method])
        if type(ncbi_taxon_id) is list:
            ncbi_taxon_id = "%0d".join(ncbi_taxon_id)
        params = {
            "identifiers": "%0d".join(protein_list),
            "species": ncbi_taxon_id,  # 9606 is human
            "caller_identity": "graphein"
        }
        params = PPIGraph._params_STRING(params, **kwargs)

        # Call STRING
        response = requests.post(request_url, data=params)
        df = pd.read_json(response.text.strip())

        return df

    @staticmethod
    def _filter_STRING(
            df: pd.DataFrame,
            **kwargs
    ) -> pd.DataFrame:
        """
        Filters results of the STRING API call according to user kwargs, keeping rows where the input parameters are
        greater or equal than the input thresholds
        :param df: Source specific Pandas dataframe (STRING) with results of the API call
        :param kwargs: User thresholds used to filter the results. The parameter names are of the form STRING_<param>,
                       where <param> is the name of the parameter. All the parameters are numerical values.
        :return: Source specific Pandas dataframe with filtered results
        """
        scores = ["score",  # combined score
                  "nscore",  # gene neighborhood score
                  "fscore",  # gene fusion score
                  "pscore",  # phylogenetic profile score
                  "ascore",  # coexpression score
                  "escore",  # experimental score
                  "dscore",  # database score
                  "tscore"]  # textmining score]
        for s in scores:
            kwarg_name = "STRING_" + s
            if kwarg_name in kwargs:
                threshold = kwargs[kwarg_name]
                df = df[df[s] >= threshold]
        return df

    @staticmethod
    def _standardise_STRING(
            df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Standardises STRING dataframe, e.g. puts everything into a common format
        :param df: Source specific Pandas dataframe
        :return: Standardised dataframe
        """
        # Rename & delete columns
        df = df.rename(columns={"preferredName_A": "p1",
                                "preferredName_B": "p2"})
        df = df[["p1", "p2"]]

        # Add source column
        df["source"] = "STRING"

        return df

    def _STRING_df(
            self
    ) -> pd.DataFrame:
        """
        Generates standardised dataframe with STRING protein-protein interactions, filtered according to user's input
        :return: Standardised dataframe with STRING interactions
        """
        df = self._parse_STRING(protein_list=self.protein_list,
                                ncbi_taxon_id=self.ncbi_taxon_id)
        df = self._filter_STRING(df, **self.kwargs)
        df = self._standardise_STRING(df)

        return df

    @staticmethod
    def _params_BIOGRID(
            params: Dict[str, Union[str, int, List[str], List[int]]],
            **kwargs
    ) -> Dict[str, Union[str, int]]:
        """
        Updates default parameters with user parameters for the method "interactions" of the BIOGRID API REST.
        See also https://wiki.thebiogrid.org/doku.php/biogridrest
        :param params: Dictionary of default parameters
        :param kwargs: User parameters for the method "network" of the BIOGRID API REST. The key must start with "BIOGRID"
        :return: Dictionary of parameters
        """
        fields = ["searchNames",  # If ‘true’, the interactor OFFICIAL_SYMBOL will be examined for a match
                  # with the geneList.
                  "max",  # Number of results to fetch
                  "interSpeciesExcluded",  # If ‘true’, interactions with interactors from different species will
                  # be excluded.
                  "selfInteractionsExcluded",  # If ‘true’, interactions with one interactor will be excluded.
                  "evidenceList",  # Any interaction evidence with its Experimental System in the list will be excluded
                  # from the results unless includeEvidence is set to true.
                  "includeEvidence",  # If set to true, any interaction evidence with its Experimental System in the
                  # evidenceList will be included in the result
                  "searchIds",  # If ‘true’, the interactor ENTREZ_GENE, ORDERED LOCUS and SYSTEMATIC_NAME (orf) will
                  # be examined for a match with the geneList.
                  "searchNames",  # If ‘true’, the interactor OFFICIAL_SYMBOL will be examined for a match with
                  # the geneList.
                  "searchSynonyms",  # If ‘true’, the interactor SYNONYMS will be examined for a match with
                  # the geneList.
                  "searchBiogridIds",  # If ‘true’, the entries in 'GENELIST' will be compared to BIOGRID internal IDS
                  # which are provided in all Tab2 formatted files.
                  "additionalIdentifierTypes",  # Identifier types on this list are examined for a match with
                  # the geneList.
                  "excludeGenes",  # If ‘true’, interactions containing genes in the geneList will be excluded from the
                  # results.
                  "includeInteractors",  # If ‘true’, in addition to interactions between genes on the geneList,
                  # interactions will also be fetched which have only one interactor on
                  # the geneList
                  "includeInteractorInteractions",  # If ‘true’ interactions between the geneList’s first order
                  # interactors will be included.
                  "pubmedList",  # Interactions will be fetched whose Pubmed Id is/ is not in this list, depending on
                  # the value of excludePubmeds.
                  "excludePubmeds",  # If ‘false’, interactions with Pubmed ID in pubmedList will be included in the
                  # results; if ‘true’ they will be excluded.
                  "htpThreshold",  # Interactions whose Pubmed ID has more than this number of interactions will be
                  # excluded from the results. Ignored if excludePubmeds is ‘false’.
                  "throughputTag"  # If set to 'low or 'high', only interactions with 'Low throughput' or
                  # 'High throughput' in the 'throughput' field will be returned.
                  ]
        for p in fields:
            kwarg_name = "BIOGRID_" + p
            if kwarg_name in kwargs:
                value = kwargs[kwarg_name]
                if type(value) is list:
                    value = "|".join(value)
                params[p] = value
        return params

    @staticmethod
    def _parse_BIOGRID(
            protein_list: List[str],
            ncbi_taxon_id: Union[int, str, List[int], List[str]],
            paginate: bool = True,
            **kwargs
    ) -> pd.DataFrame:
        """
        Makes BIOGRID API call and returns a source specific Pandas dataframe.
        See also [1] BIOGRID: https://wiki.thebiogrid.org/doku.php/biogridrest
        :param protein_list: Proteins to include in the graph
        :param ncbi_taxon_id: NCBI taxonomy identifiers for the organism. Default is 9606 (Homo Sapiens)
        :param paginate: boolean indicating whether to paginate the calls (for BIOGRID, the maximum number of rows per
                         call is 10000). Defaults to True
        :param kwargs: Parameters of the "interactions" method of the BIOGRID API REST, used to select the results.
                       The parameter names are of the form BIOGRID_<param>, where <param> is the name of the parameter.
                       Information about these parameters can be found at [1].
        :return: Source specific Pandas dataframe.
        """
        # Prepare call to BIOGRID API
        string_api_url = "https://webservice.thebiogrid.org"
        method = "interactions"
        request_url = "/".join([string_api_url, method])
        if type(ncbi_taxon_id) is list:
            ncbi_taxon_id = "|".join(ncbi_taxon_id)
        params = {  # Default parameters
            "geneList": "|".join(protein_list),
            "accesskey": "c4ab86373e0bb921a878bb6d15ee4fb4",
            "taxId": ncbi_taxon_id,  # 9606 is human
            "format": "json",
            "max": 10000,  # Number of results to fetch
            "searchNames": "true",
            "includeInteractors": "false",  # Set to true to get any interaction involving EITHER gene,
            # set to false to get interactions between genes
            "selfInteractionsExcluded": "true"  # If ‘true’, interactions with one interactor will be excluded
        }
        params = PPIGraph._params_BIOGRID(params, **kwargs)

        # Call BIOGRID
        def make_call(request_url, params, start=0, max=10000, paginate=paginate):
            params["start"] = start
            response = requests.post(request_url, data=params)
            df = pd.read_json(response.text.strip()).transpose()

            # Maximum number of results is limited to 10k. Paginate to retrieve everything
            if paginate and df.shape[0] == max:
                next_df = make_call(request_url, params, start + max, max)
                df = pd.concat([df, next_df])

            return df

        return make_call(request_url=request_url,
                         params=params,
                         start=0,
                         max=params['max'])

    @staticmethod
    def _filter_BIOGRID(
            df: pd.DataFrame,
            **kwargs
    ) -> pd.DataFrame:
        """
        Filters results of the BIOGRID API call according to user kwargs.
        :param df: Source specific Pandas dataframe (BIOGRID) with results of the API call
        :param kwargs: User thresholds used to filter the results. The parameter names are of the form BIOGRID_<param>,
                       where <param> is the name of the parameter. All the parameters are numerical values.
        :return: Source specific Pandas dataframe with filtered results
        """
        # Note: To filter BIOGRID interactions, use parameters from https://wiki.thebiogrid.org/doku.php/biogridrest
        # TODO: Make sure that user can filter results of API call via the parameters.
        #       Otherwise implement filtering here.
        # TODO: Perhaps can filter by EXPERIMENTAL_SYSTEM (e.g. Co-fractionation)
        #       and EXPERIMENTAL_SYSTEM_TYPE (e.g. physical)
        return df

    @staticmethod
    def _standardise_BIOGRID(
            df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Standardises BIOGRID dataframe, e.g. puts everything into a common format
        :param df: Source specific Pandas dataframe
        :return: Standardised dataframe
        """
        # Rename & delete columns
        df = df.rename(columns={"OFFICIAL_SYMBOL_A": "p1",
                                "OFFICIAL_SYMBOL_B": "p2"})
        df = df[["p1", "p2"]]

        # Add source column
        df["source"] = "BIOGRID"

        return df

    def _BIOGRID_df(
            self
    ) -> pd.DataFrame:
        """
        Generates standardised dataframe with BIOGRID protein-protein interactions, filtered according to user's input
        :return: Standardised dataframe with BIOGRID interactions
        """
        df = self._parse_BIOGRID(protein_list=self.protein_list,
                                 ncbi_taxon_id=self.ncbi_taxon_id,
                                 **self.kwargs)
        df = self._filter_BIOGRID(df, **self.kwargs)
        df = self._standardise_BIOGRID(df)
        return df

    def _source_df(
            self,
            source: str
    ) -> pd.DataFrame:
        """
        Loads standardised dataframe for specified source
        :param source: string indicating the source
        :return: Standardised dataframe with the source interactions
        """
        df = None
        if source.upper() == "STRING":
            df = self._STRING_df()
            if self.verbose:
                print('Selected {} STRING interactions'.format(df.shape[0]))
        elif source.upper() == "BIOGRID":
            df = self._BIOGRID_df()
            if self.verbose:
                print('Selected {} BIOGRID interactions'.format(df.shape[0]))
        else:
            raise ValueError("Source '{}' is not supported".format(source))
        return df

    def _ppi_df(
            self
    ) -> pd.DataFrame:
        """
        Generates a unified Pandas dataframe with the protein-protein interactions from all the input sources
        :return: Unified dataframe with all the interactions
        """
        # High-level view of the implemented algorithm:
        # Loop over user sources
        #   Load interactions from source
        #   Filter interactions according to user params
        #   Standardise sources
        # Merge all sources into single df
        dfs = [self._source_df(s) for s in self.sources]
        df = pd.concat(dfs)
        return df

    def nx_graph(
            self
    ) -> nx.Graph:
        """
        Produces networkx graph of the PPI interactions
        :return: nx.Graph
        """
        df = self._ppi_df()
        g = nx.from_pandas_edgelist(df=df,
                                    source="p1",
                                    target="p2",
                                    edge_attr=None)
        return g


if __name__ == "__main__":
    """
    pg = ProteinGraph(granularity='CA', insertions=False, keep_hets=True,
                      node_featuriser='meiler',
                      allowed_interactions=None,
                      get_contacts_path='/home/arj39/Documents/github/getcontacts',
                      pdb_dir='/home/arj39/Documents/test/pdb/', contacts_dir='/home/arj39/Documents/test/contacts/',
                      exclude_waters=True, covalent_bonds=False, include_ss=True, include_ligand=False,
                      edge_distance_cutoff=5
                      # node_featuriser=dgl.data.chem.atom_type_one_hot(),
                      # edge_featuriser=dgl.data.chem.bond_type_one_hot(),
                      # graph_constructor=dgl.data.chem.mol_to_graph())
                      )
    """
    """
    pg = ProteinGraph(granularity='CA', insertions=False, keep_hets=True,
                      node_featuriser='meiler',
                      allowed_interactions=None,
                      get_contacts_path='/home/arj39/Documents/github/getcontacts',
                      pdb_dir='/home/arj39/Documents/test/pdb/', contacts_dir='/home/arj39/Documents/test/contacts/',
                      exclude_waters=True, covalent_bonds=False, include_ss=True, include_ligand=False,
                      edge_distance_cutoff=10
                      # node_featuriser=dgl.data.chem.atom_type_one_hot(),
                      # edge_featuriser=dgl.data.chem.bond_type_one_hot(),
                      # graph_constructor=dgl.data.chem.mol_to_graph())
                      )
    """

    pg = ProteinGraph(
        granularity="CA",
        insertions=False,
        keep_hets=False,
        intramolecular_interactions=None,
        get_contacts_path="/Users/arianjamasb/github/getcontacts",
        pdb_dir="../examples/pdbs/",
        contacts_dir="../examples/contacts/",
        exclude_waters=True,
        covalent_bonds=False,
        include_ss=True,
        include_ligand=False,
        verbose=True,
        long_interaction_threshold=5,
        edge_distance_cutoff=10,
        edge_featuriser=None,
        node_featuriser="meiler",
    )

    g = pg.dgl_graph_from_pdb_code(
        "3eiy",
        chain_selection="all",
        edge_construction=["distance", "delaunay"],  # , 'delaunay', 'k_nn'],
        encoding=False,
        k_nn=None,
    )

    pg.torch_geometric_graph_from_pdb_code(
        "3eiy",
        chain_selection="all",
        edge_construction=["distance", "delaunay"],
        encoding=True,
        k_nn=None,
    )

    g, _, __ = pg.nx_graph_from_pdb_code(
        pdb_code="3eiy",
        chain_selection="all",
        edge_construction=["contacts"],
        encoding=True,
    )

    rg = RNAGraph()
    g = rg.dgl_graph_from_dotbracket("((((((....))))))", sequence="AUGCAUGCAUGCAUGC")
    # pg.make_atom_graph(pdb_code='3eiy')

    # Check KNN
    # g, resiude_name_encoder, residue_id_encoder = pg.nx_graph_from_pdb_code('3eiy', chain_selection='all',
    #                                                                        edge_construction=['distance', 'contacts'],
    #                                                                        encoding=True)

    # Sanity checks for small PPI
    protein_list = ["CDC42", "CDK1", "KIF23", "PLK1", "RAC2", "RACGAP1", "RHOA", "RHOB"]
    sources = ["STRING", "BIOGRID"]
    kwargs = {"STRING_escore": 0.2,  # Keeps STRING interactions with an experimental score >= 0.2
              "BIOGRID_throughputTag": "high"  # Keeps high throughput BIOGRID interactions
              }
    ppi_graph = PPIGraph(protein_list=protein_list,
                         sources=sources,
                         **kwargs)

    # BIOGRID
    df = ppi_graph._parse_BIOGRID(protein_list, 9606, **kwargs)
    assert (df["THROUGHPUT"] == "High Throughput").all()

    # STRING
    df = ppi_graph._parse_STRING(protein_list=ppi_graph.protein_list,
                                 ncbi_taxon_id=ppi_graph.ncbi_taxon_id)
    df = ppi_graph._filter_STRING(df, **ppi_graph.kwargs)
    assert (df["escore"] >= 0.2).all()
