"""This module contains the ExplicitMPS class."""

from functools import reduce
from copy import deepcopy
from typing import Iterable
import numpy as np
from opt_einsum import contract

from mpopt.mps.canonical import CanonicalMPS
from mpopt.utils.utils import kron_tensors


class ExplicitMPS:
    """Class for finite-size explicit matrix product states (MPS) with open boundary conditions.

    Notes
    -----
    Hereafter by saying the MPS is in an explicit form we mean that
    the state is stored in the following format: for each three-dimensional tensor
    at site `i`, there exists a singular values diagonal matrix at bond `i`.
    For "ghost" bonds at indices `0`, `L-1` (i.e., bonds of dimension `1`),
    the corresponding singular value tensors at the boundaries
    would be the identities of the same dimension.
    We index sites with `i` from `0` to `L-1`, with bond `i` being left of site `i`.
    Essentially, it corresponds to storing each `Γ[i]` and `Λ[i]` as shown in
    fig.4b in reference [1]_.

    ```
           i     i    i+1    i
    ...---[ ]---( )---[ ]---( )---...
                 |           |
                 |           |
    ```

    .. [1] Hauschild, J. and Pollmann, F., 2018.
       Efficient numerical simulations with tensor networks:
       Tensor Network Python (TeNPy). SciPost Physics Lecture Notes, p.005.

    Attributes:
        tensors :
            The "physical" tensors of the MPS, one for each physical site.
            Each tensor has legs (virtual left, physical, virtual right), in short `(vL, i, vR)`.
        singular_values :
            The singular values at each of the bonds, `singular_values[i]` is left of `tensors[i]`.
            Each singular values list at each bond is normalised to 1.
        num_sites :
            Number of sites.
        num_bonds :
            Number of non-trivial bonds: `num_sites - 1`.
        tolerance :
            Absolute tolerance of the normalisation of the singular value spectrum at each bond.

    Exceptions:
        ValueError:
            If `tensors` and `singular_values` do not have corresponding lengths.
            The number of singular value matrices should be equal to the number of tensors + 1,
            because there are two trivial singular value matrices at each of the ghost bonds.
    """

    def __init__(
        self,
        tensors: list[np.ndarray],
        singular_values: list[list],
        tolerance: np.float64 = 1e-12,
        chi_max: np.int32 = 1e4,
    ):

        self.tensors = tensors
        self.num_sites = len(tensors)
        self.num_bonds = self.num_sites - 1
        self.bond_dimensions = [self.tensors[i].shape[2] for i in range(self.num_bonds)]
        self.phys_dimensions = [self.tensors[i].shape[1] for i in range(self.num_sites)]
        self.singular_values = singular_values
        self.num_singval_mat = len(singular_values)
        self.dtype = tensors[0].dtype
        self.tolerance = tolerance
        self.chi_max = chi_max

        if self.num_sites != self.num_singval_mat - 1:
            raise ValueError(
                f"The number of tensors {self.num_sites} should correspond "
                "to the number of non-trivial singular value matrices "
                f"{len(tensors) - 1}, instead the number of "
                f"non-trivial singular value matrices is {self.num_singval_mat - 2}."
            )

        for i, tensor in enumerate(tensors):
            if len(tensor.shape) != 3:
                raise ValueError(
                    "A valid MPS tensor must have 3 legs"
                    f"while the one given has {len(tensor.shape)}."
                )

        for i, _ in enumerate(singular_values):
            norm = np.linalg.norm(singular_values[i])
            if abs(norm - 1) > tolerance:
                raise ValueError(
                    "The norm of each singular values tensor must be 1, "
                    f"instead the norm is {norm} at bond {i + 1}."
                )

    def __len__(self) -> np.int32:
        """Returns the number of sites in the MPS."""
        return self.num_sites

    def __iter__(self) -> Iterable:
        """Returns an iterator over (singular_values, tensors) pair for each site."""
        return zip(self.singular_values, self.tensors)

    def copy(self) -> "ExplicitMPS":
        """Returns a copy of the current MPS."""
        return ExplicitMPS(
            deepcopy(self.tensors),
            deepcopy(self.singular_values),
            self.tolerance,
            self.chi_max,
        )

    def reverse(self) -> "ExplicitMPS":
        """Returns a reversed version of a given MPS."""

        reversed_tensors = list(np.transpose(t) for t in reversed(self.tensors))
        reversed_singular_values = list(reversed(self.singular_values))

        return ExplicitMPS(
            reversed_tensors, reversed_singular_values, self.tolerance, self.chi_max
        )

    def conjugate(self) -> "ExplicitMPS":
        """Returns a complex-conjugated version of the current MPS."""

        conjugated_tensors = [np.conjugate(tensor) for tensor in self.tensors]
        conjugated_sing_vals = [
            np.conjugate(sing_vals) for sing_vals in self.singular_values
        ]
        return ExplicitMPS(
            conjugated_tensors,
            conjugated_sing_vals,
            self.tolerance,
            self.chi_max,
        )

    def single_site_left_iso(self, site: int) -> np.ndarray:
        """Computes a single-site left isometry at a given site."""

        if site not in range(self.num_sites):
            raise ValueError(
                f"Site given {site}, with the number of sites in the MPS {self.num_sites}."
            )

        return np.tensordot(
            np.diag(self.singular_values[site]), self.tensors[site], (1, 0)
        )

    def single_site_right_iso(self, site: int) -> np.ndarray:
        """Computes a single-site right isometry at a given site."""

        if site not in range(self.num_sites):
            raise ValueError(
                f"Sites given {site}, {site + 1}, "
                f"with the number of sites in the MPS {self.num_sites}."
            )

        return np.tensordot(
            self.tensors[site], np.diag(self.singular_values[site + 1]), (2, 0)
        )

    def single_site_left_iso_iter(self) -> Iterable:
        """Returns an iterator over the left isometries for every site."""

        return (self.single_site_left_iso(i) for i in range(self.num_sites))

    def single_site_right_iso_iter(self) -> Iterable:
        """Returns an iterator over the right isometries for every site."""

        return (self.single_site_right_iso(i) for i in range(self.num_sites))

    def two_site_left_iso(self, site: int) -> np.ndarray:
        """Computes a two-site isometry on a given site and
        the following one from two single-site left isometries.
        """

        if site not in range(self.num_sites):
            raise ValueError(
                f"Sites given {site}, {site + 1}, "
                f"with the number of sites in the MPS {self.num_sites}."
            )

        return np.tensordot(
            self.single_site_left_iso(site),
            self.single_site_left_iso(site + 1),
            (2, 0),
        )

    def two_site_right_iso(self, site: int) -> np.ndarray:
        """Computes a two-site isometry on a given site and
        the following one from two single-site right isometries.
        """

        if site not in range(self.num_sites):
            raise ValueError(
                f"Sites given {site}, {site + 1}, "
                f"with the number of sites in the MPS {self.num_sites}."
            )

        return np.tensordot(
            self.single_site_right_iso(site),
            self.single_site_right_iso(site + 1),
            (2, 0),
        )

    def two_site_right_iso_iter(self) -> Iterable:
        """Returns an iterator over the two-site right isometries for every site and
        its right neighbour.
        """
        return (self.two_site_right_iso(i) for i in range(self.num_sites))

    def two_site_left_iso_iter(self) -> Iterable:
        """Returns an iterator over the two-site left isometries for every site and
        its right neighbour.
        """
        return (self.two_site_left_iso(i) for i in range(self.num_sites))

    def dense(self, flatten: bool = True) -> np.ndarray:
        """Returns dense representation of the MPS.

        Attention: will cause memory overload for number of sites > ~20!
        """

        tensors = list(self.single_site_right_iso_iter())
        dense = reduce(lambda a, b: np.tensordot(a, b, (-1, 0)), tensors)

        if flatten:
            return dense.flatten()

        return dense

    def density_mpo(self) -> list[np.ndarray]:
        """
        Returns the MPO representation (as a list of tensors)
        of the density matrix defined by a given MPS.
        Each tensor in the MPO list has legs (vL, vR, pU, pD),
        where v stands for "virtual", p -- for "physical",
        and L, R, U, D stand for "left", "right", "up", "down".

        This operation is depicted in the following picture.
        In the cartoon, `{i,j,k,l}` and `{a,b,c,d}` are single indices,
        while `ab` and `cd` denote multi indices.
        Here, the ( )'s represent the MPS tensors, the O's ---
        the singular values tensors, the [ ]'s --- the MPO tensors.
        The MPS with the physical legs up is complex-conjugated element-wise,
        this is denoted by the star sign.
        The empty line between the MPS and its complex-conjugated version
        stands for the tensor (kronecker) product.

        ```
               i          j
          a    |          |        c           i     j
        ...---(*)---O*---(*)---O*---...    ab  |     |  cd
                                    --> ...---[ ]---[ ]---...
        ...---( )---O----( )---O----...        |     |
          b    |          |        d           k     l
               k          l
        ```

        """

        tensors = list(self.single_site_right_iso_iter())

        mpo = map(
            lambda t: kron_tensors(
                t, t, conjugate_second=True, merge_physicals=False
            ).transpose((0, 3, 2, 1)),
            tensors,
        )

        return list(mpo)

    def entanglement_entropy(self) -> np.ndarray:
        """Returns the entanglement entropy for bipartitions at each of the bonds."""

        def xlogx(arg):
            if arg == 0:
                return 0
            return arg * np.log(arg)

        entropy = np.zeros(shape=(self.num_bonds,), dtype=np.float64)

        for bond in range(self.num_bonds):
            singular_values = self.singular_values[bond].copy()
            singular_values[singular_values < self.tolerance] = 0
            singular_values2 = singular_values * singular_values
            entropy[bond] = -np.sum(
                np.fromiter((xlogx(s) for s in singular_values2), dtype=np.float64)
            )
        return entropy

    def right_canonical(self) -> CanonicalMPS:
        """Returns the MPS in the right-canonical form given the MPS in the explicit form.

        (see eq.19 in https://scipost.org/10.21468/SciPostPhysLectNotes.5 for reference),
        """

        return CanonicalMPS(
            list(self.single_site_right_iso_iter()),
            orth_centre=None,
            tolerance=self.tolerance,
            chi_max=self.chi_max,
        )

    def left_canonical(self) -> CanonicalMPS:
        """Returns the MPS in the left-canonical form given the MPS in the explicit form.

        (see eq.19 in https://scipost.org/10.21468/SciPostPhysLectNotes.5 for reference),
        """

        return CanonicalMPS(
            list(self.single_site_left_iso_iter()),
            orth_centre=None,
            tolerance=self.tolerance,
            chi_max=self.chi_max,
        )

    def mixed_canonical(self, orth_centre: int) -> CanonicalMPS:
        """Returns the MPS in the mixed-canonical form
        with the orthogonality centre being located at `orth_centre`.

        Arguments:
            orth_centre_index: int
                An integer which can take values `0, 1, ..., num_sites-1`.
                Denotes the position of the orthogonality centre --
                the only non-isometry in the new MPS.
        """

        if orth_centre not in range(self.num_sites):
            raise ValueError(
                f"Orthogonality centre index given {orth_centre}, "
                f"with the number of sites in the MPS {self.num_sites}."
            )

        mixed_can_mps = []

        for i in range(orth_centre):
            mixed_can_mps.append(self.single_site_left_iso(i))

        orth_centre_tensor = contract(
            "ij, jkl, lm -> ikm",
            np.diag(self.singular_values[orth_centre]),
            self.tensors[orth_centre],
            np.diag(self.singular_values[orth_centre + 1]),
            optimize=[(0, 1), (0, 1)],
        )
        mixed_can_mps.append(orth_centre_tensor)

        for i in range(orth_centre + 1, self.num_sites):
            mixed_can_mps.append(self.single_site_right_iso(i))

        return CanonicalMPS(
            mixed_can_mps,
            orth_centre=orth_centre,
            tolerance=self.tolerance,
            chi_max=self.chi_max,
        )
