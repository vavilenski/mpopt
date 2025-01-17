"""
In this experiment, we decode a classical linear error correction code.
First, we build the MPS containing the superposition of all codewords.
Then, we demostrate simple decoding of a classical LDPC code using Dephasing DMRG --
our own built-in DMRG-like optimisation algorithm to solve the main component problem --
the problem of finding a computational basis state cotributing the most to a given state.
"""

import sys
from typing import Union, Optional
from functools import reduce

import numpy as np
import qecstruct as qec
from more_itertools import powerset
from tqdm import tqdm

sys.path[0] += "/.."

from mpopt.mps.explicit import ExplicitMPS
from mpopt.mps.canonical import CanonicalMPS
from mpopt.contractor.contractor import apply_one_site_operator, mps_mpo_contract
from mpopt.mps.utils import (
    create_custom_product_state,
    create_simple_product_state,
    inner_product,
    find_orth_centre,
)
from mpopt.optimiser.dephasing_dmrg import DephasingDMRG as deph_dmrg
from mpopt.utils.utils import mpo_to_matrix


class ConstraintString:
    """
    Class for storing a string of logical constraints in the
    Matrix Product Operator format.
    Logical constraints are passed in the form of 4-dimensional MPO tensors.

    Attributes:
        constraints : list
            A list of logical constraints of which the string consists.
        sites : list of lists
            Each list inside corresponds to a constraint from the `constraints` list,
            and contains the sites to which each constraint is applied.
            For example, [[3, 5], [2, 4, 6], ...] means applying
            `constraints[0]` to sites 3 and 5, `constraints[1]` to sites 2, 4, 6, etc.

    Exceptions:
        ValueError:
            Empty list of constraints.
        ValueError:
            Empty list of sites.
        ValueError:
            The `sites` list is longer than the `constraints` list.
        ValueError:
            Non-unique sites in the `sites` list.
    """

    def __init__(self, constraints: list[np.ndarray], sites: list[list[np.int32]]):
        self.constraints = constraints
        self.sites = sites

        if self.constraints == []:
            raise ValueError("Empty list of constraints passed.")

        if self.sites == []:
            raise ValueError("Empty list of sites passed.")

        if len(self.sites) > len(self.constraints):
            raise ValueError(
                f"We have {len(self.constraints)} constraints in the constraints list, "
                f"{len(self.sites)} constraints assumed by the sites list."
            )

        seen = set()
        uniq = [site for site in self.flat() if site not in seen and not seen.add(site)]
        if uniq != self.flat():
            raise ValueError("Non-unique sites encountered in the list.")

    def __getitem__(self, index: np.int32) -> tuple:
        """
        Returns the pair of a list of sites together with the corresponding MPO.

        Arguments:
            index :
                The index of the list of sites.
        """

        return self.sites[index], self.constraints[index]

    def flat(self) -> list[np.int32]:
        """
        Returns a flattened list of sites.
        """

        return [item for sites in self.sites for item in sites]

    def span(self) -> np.int32:
        """
        Returns the span (length) of the constraint string.
        """

        return max(self.flat()) - min(self.flat()) + 1

    def get_mpo(self) -> list[np.ndarray]:
        """
        Returns the constraint string in the MPO format.
        Note, that it will not include identities, which means
        it needs to be manually adjusted to a corresponding MPS,
        as the MPO can be smaller in size.
        """

        mpo = [None for _ in range(self.span())]
        for index, sites_sites in enumerate(self.sites):
            for site in sites_sites:
                mpo[site - min(self.flat())] = self.constraints[index]

        return mpo


# Below, we define some decoding-specific functions over the MPS/MPO entities
# we encounter in the decoding process.


def bias_channel(p_bias: np.float64 = 0.5, which: str = "0") -> np.ndarray:
    """
    Here, we define a bias channel -- the operator which will bias us towards the initial message
    while decoding by ranking the bitstrings according to Hamming distance from the latter.
    This function returns a single-site MPO,
    corresponding to the bias channel, which acts on basis states,
    i.e., |0> and |1>, as follows:
    |0> -> √(1-p)|0> + √p|1>,
    |1> -> √(1-p)|1> + √p|0>.
    Note, that this operation is unitary, which means it keeps the canonical form.

    Arguments:
        p_bias :
            Probability of the channel.
        which :
            "0" or "1", depending on which single-qubit state we are acting on.

    Returns:
        b_ch:
            The corresponding single-qubit MPO.
    """

    if not 0 <= p_bias <= 1:
        raise ValueError(
            f"The channel parameter `p_bias` should be a probability, "
            f"given {p_bias}."
        )
    if which not in ["0", "1"]:
        raise ValueError("Invalid qubit basis state given.")

    if which == "0":
        b_channel = np.array(
            [
                [np.sqrt(1 - p_bias), np.sqrt(p_bias)],
                [np.sqrt(p_bias), -np.sqrt(1 - p_bias)],
            ]
        )
    if which == "1":
        b_channel = np.array(
            [
                [-np.sqrt(1 - p_bias), np.sqrt(p_bias)],
                [np.sqrt(p_bias), np.sqrt(1 - p_bias)],
            ]
        )

    return b_channel


def apply_bias_channel(
    basis_mps: Union[ExplicitMPS, CanonicalMPS],
    codeword_string: str,
    prob_channel: np.float64 = 0.5,
) -> CanonicalMPS:
    """
    The function which applies a bias channel to a computational-basis-state MPS.

    Arguments:
        basis_mps :
            The computational-basis-state MPS, e.g., |010010>.
        codeword_string:
            The string of "0" and "1" which corresponds to `basis_mps`.
        prob_channel :
            The bias channel probability.


    Returns:
        biased_mps:
            The resulting MPS.
    """

    if len(basis_mps) != len(codeword_string):
        raise ValueError(
            f"The lengths of `basis_mps` and `codeword_string` should be equal,"
            f"given {len(basis_mps)}."
        )

    if isinstance(basis_mps, ExplicitMPS):
        basis_mps = basis_mps.right_canonical()

    biased_mps_tensors = []
    for i, mps_tensor in enumerate(basis_mps.tensors):
        biased_mps_tensors.append(
            apply_one_site_operator(
                tensor=mps_tensor,
                operator=bias_channel(prob_channel, which=codeword_string[i]),
            )
        )

    return CanonicalMPS(biased_mps_tensors)


# Below, we define some utility functions to operate with data structures from `qecstruct` --
# an error-correction library we are using in this example.


def bin_vec_dense(vector: "qec.sparse.BinaryVector") -> np.ndarray:
    """
    Given a vector (1D array) in the :class:`qecstruct.sparse.BinaryVector` format
    (native to `qecstruct`), returns its dense representation.

    Arguments:
        vector :
            The `BinaryVector` object.

    Returns:
        array :
            The dense representation.
    """

    array = np.zeros(vector.len(), dtype=np.int32)
    for pos in vector:
        array[pos] = 1
    return array


def linear_code_checks(code: qec.LinearCode) -> list[tuple[np.ndarray]]:
    """
    Given a linear code, returns a list of its checks, where each check
    is represented as a list of indices of the bits adjacent to it.

    Arguments:
        code :
            Linear code object.

    Returns:
        checks : list of lists
            List of checks.
    """

    parity_matrix = code.par_mat()
    array = np.zeros(
        (parity_matrix.num_rows(), parity_matrix.num_columns()), dtype=np.int32
    )
    for row, cols in enumerate(parity_matrix.rows()):
        for col in cols:
            array[row, col] = 1
    return [np.nonzero(row)[0] for row in array]


def get_codewords(code: qec.LinearCode) -> np.ndarray:
    """
    Returns the list of codewords of a linear code. Codewords are returned
    as integers in most-significant-bit-first convention.

    Arguments:
        code : :class:`qecstruct.LinearCode`
            Linear code object.

    Returns:
        codewords :
            List of codewords.
    """

    codewords = []

    gen_mat = code.gen_mat()
    rows_bin = gen_mat.rows()
    rows_dense = [bin_vec_dense(row_bin) for row_bin in rows_bin]
    rows_int = [row.dot(1 << np.arange(row.size)[::-1]) for row in rows_dense]

    # Append the all-zeros codeword which is always a codeword.
    codewords.append(0)

    # Append the rows of the generator matrix.
    for basis_codeword in rows_int:
        codewords.append(basis_codeword)

    # Append all linear combinations.
    for generators in powerset(rows_int):
        if len(generators) > 1:
            codewords.append(reduce(np.bitwise_xor, generators))

    return np.sort(np.array(codewords))


def get_constraint_sites(code: qec.LinearCode) -> list[np.int32]:
    """
    Returns the list of MPS sites where the logical constraints should be applied.

    Arguments:
        code : :class:`qecstruct.LinearCode`
            Linear code object.

    Returns:
        strings : list of ints
            List of MPS sites.
    """

    sites_all = linear_code_checks(code)
    check_degree = len(sites_all[0])
    constraints_strings = []

    for sites in sites_all:

        # Retreiving the sites indices where we apply the "bulk"/"boundary" XOR tensors.
        xor_left_sites = [sites[0]]
        xor_bulk_sites = [sites[i] for i in range(1, check_degree - 1)]
        xor_right_sites = [sites[-1]]

        # Retreiving the sites indices where we apply the SWAP tensors.
        swap_sites = list(range(sites[0] + 1, sites[-1]))
        for k in range(1, check_degree - 1):
            swap_sites.remove(sites[k])

        constraints_strings.append(
            [xor_left_sites, xor_bulk_sites, swap_sites, xor_right_sites]
        )

    return constraints_strings


def prepare_codewords(
    code: qec.LinearCode,
    prob_error: np.float64 = 0.5,
    error_model: "qec.noise_model" = qec.BinarySymmetricChannel,
    seed: Optional[np.int32] = None,
) -> tuple[str, str]:
    """
    This function prepares a codeword and its copy after applying an error model.

    Arguments:
    code :
        Linear code object.
    prob_error :
        Error probability of the error model.
    error_model :
        The error model used to flip bits of a random codeword.
    seed :
        Random seed.

    Returns:
        initial_codeword : str
            The bitstring of the initial codeword.
        perturbed_codeword : str
            The bitstring of the perturbed codeword.
    """

    num_bits = len(code)
    initial_codeword = code.random_codeword(qec.Rng(seed))
    perturbed_codeword = initial_codeword + error_model(prob_error).sample(
        num_bits, qec.Rng(seed)
    )
    initial_codeword = "".join(str(bit) for bit in bin_vec_dense(initial_codeword))
    perturbed_codeword = "".join(str(bit) for bit in bin_vec_dense(perturbed_codeword))

    return initial_codeword, perturbed_codeword


# The functions below are used to apply constraints to a codeword MPS and perform actual decoding.


def apply_parity_constraints(
    codeword_state: Union[ExplicitMPS, CanonicalMPS],
    strings: list[list[list[np.int32]]],
    logical_tensors: list[np.ndarray],
    chi_max: np.int32 = 1e4,
    renormalise: bool = False,
    strategy: str = "naive",
    silent: bool = False,
) -> CanonicalMPS:
    """
    A function applying constraints to a codeword MPS.

    Arguments:
        codeword_state :
            The codeword MPS.
        strings :
            The list of arguments for :class:`ConstraintString`.
        logical_tensors
            List of logical tensors for :class:`ConstraintString`.
        chi_max :
            Maximum bond dimension to keep in the contractor.
        renormalise :
            To (not) renormalise the singular values at each MPS bond involved in contraction.
        strategy :
            The contractor strategy.
        silent :
            Whether to show the progress bar or not.

    Returns:
        codeword_state :
            The resulting MPS.
    """

    if strategy == "naive":

        for string in tqdm(strings, disable=silent):

            # Preparing the MPO.
            string = ConstraintString(logical_tensors, string)
            mpo = string.get_mpo()

            # Finding the starting site for the MPS to perform contraction.
            start_site = min(string.flat())

            # Preparing the MPS for contraction.
            if isinstance(codeword_state, ExplicitMPS):
                codeword_state = codeword_state.mixed_canonical(orth_centre=start_site)

            if isinstance(codeword_state, CanonicalMPS):

                if codeword_state.orth_centre is None:
                    orth_centres, flags_left, flags_right = find_orth_centre(
                        codeword_state, return_orth_flags=True
                    )

                    # Managing possible issues with multiple orthogonality centres
                    # arising if we do not renormalise.
                    if not orth_centres and len(orth_centres) == 1:
                        codeword_state.orth_centre = orth_centres[0]
                    # Convention.
                    if all(flags_left) and all(flags_right):
                        codeword_state.orth_centre = 0
                    elif flags_left in (
                        [True] + [False] * (codeword_state.num_sites - 1)
                    ):
                        if flags_right == [not flag for flag in flags_left]:
                            codeword_state.orth_centre = codeword_state.num_sites - 1
                    elif flags_left in (
                        [True] * (codeword_state.num_sites - 1) + [False]
                    ):
                        if flags_right == [not flag for flag in flags_left]:
                            codeword_state.orth_centre = 0
                    elif all(flags_right):
                        codeword_state.orth_centre = 0
                    elif all(flags_left):
                        codeword_state.orth_centre = codeword_state.num_sites - 1

                codeword_state = codeword_state.move_orth_centre(final_pos=start_site)

            # Doing the contraction.
            codeword_state = mps_mpo_contract(
                codeword_state,
                mpo,
                start_site,
                renormalise=renormalise,
                chi_max=chi_max,
                inplace=False,
            )

    return codeword_state


def decode(
    message: Union[ExplicitMPS, CanonicalMPS],
    codeword: Union[ExplicitMPS, CanonicalMPS],
    code: qec.LinearCode,
    num_runs: np.int32 = 1,
    chi_max_dmrg: np.int32 = 1e4,
    cut: np.float64 = 1e-12,
    silent: bool = False,
) -> tuple[deph_dmrg, np.float64]:
    """
    This function performs actual decoding of a message given a code and
    the DMRG truncation parameters.
    Returns the overlap between the decoded message given the initial message.

    Arguments:
        message :
            The message MPS.
        codeword :
            The codeword MPS.
        code :
            Linear code object.
        num_runs : int
            Number of DMRG sweeps.
        chi_max_dmrg :
            Maximum bond dimension to keep in the DMRG algorithm.
        cut :
            The lower boundary of the spectrum in the DMRG algorithm.
            All the singular values smaller than that will be discarded.

    Returns:
        engine :
            The container class for the DMRG, see :class:`mpopt.optimiser.DMRG`.
        overlap :
            The overlap between the decoded message and a given codeword,
            computed as the following inner product |<decoded_message|codeword>|.
    """

    # Creating an all-plus state to start the DMRG with.
    num_bits = len(code)
    mps_dmrg_start = create_simple_product_state(num_bits, which="+")
    engine = deph_dmrg(
        mps_dmrg_start,
        message,
        chi_max=chi_max_dmrg,
        cut=cut,
        mode="LA",
        silent=silent,
    )
    engine.run(num_runs)
    mps_dmrg_final = engine.mps.right_canonical()
    overlap = abs(inner_product(mps_dmrg_final, codeword))

    return engine, overlap


if __name__ == "__main__":

    # Fixing a random seed
    SEED = 123

    # Here, we define the tensors which represent logical constraints.
    # We use the following tensors: XOR, SWAP, IDENTITY.
    # See the notes for additional information.

    # According to our convention, each tensor has legs (vL, vR, pU, pD),
    # where v stands for "virtual", p -- for "physical",
    # and L, R, U, D stand for "left", "right", "up", "down".

    IDENTITY = np.eye(2).reshape((1, 1, 2, 2))
    XOR_BULK = np.fromfunction(
        lambda i, j, k, l: (i ^ j ^ k ^ 1) * np.eye(2)[k, l],
        (2, 2, 2, 2),
        dtype=np.int32,
    )
    XOR_LEFT = np.fromfunction(
        lambda i, j, k, l: np.eye(2)[j, k] * np.eye(2)[k, l],
        (1, 2, 2, 2),
        dtype=np.int32,
    )
    XOR_RIGHT = np.fromfunction(
        lambda i, j, k, l: np.eye(2)[i, k] * np.eye(2)[k, l],
        (2, 1, 2, 2),
        dtype=np.int32,
    )
    SWAP = np.fromfunction(
        lambda i, j, k, l: np.eye(2)[i, j] * np.eye(2)[k, l],
        (2, 2, 2, 2),
        dtype=np.int32,
    )
    tensors = [XOR_LEFT, XOR_BULK, SWAP, XOR_RIGHT]

    # Defining the parameters of a classical LDPC code.
    NUM_BITS, NUM_CHECKS = 10, 6
    CHECK_DEGREE, BIT_DEGREE = 5, 3
    if NUM_BITS / NUM_CHECKS != CHECK_DEGREE / BIT_DEGREE:
        raise ValueError("The Tanner graph of the code must be bipartite.")

    # Constructing the code as a qecstruct object.
    example_code = qec.random_regular_code(
        NUM_BITS, NUM_CHECKS, BIT_DEGREE, CHECK_DEGREE, qec.Rng(SEED)
    )

    # Preparing the initial state.
    state = create_simple_product_state(NUM_BITS, which="+")
    state_dense = state.dense(flatten=True)

    # Getting the sites where each string of constraints should be applied.
    code_constraint_sites = get_constraint_sites(example_code)

    print(
        "__________________________________________________________________________________________"
    )
    print("")
    print("Checking the codeword superposition state:")
    print("")

    # Preparing the codeword superposition state by the MPS-MPO evolution.
    state = apply_parity_constraints(state, code_constraint_sites, tensors)

    # Preparing the codeword superposition state in the dense form.
    for j in range(NUM_CHECKS):

        # Preparing the MPO.
        constraint_string = ConstraintString(tensors, code_constraint_sites[j])
        constraint_mpo = constraint_string.get_mpo()

        # Finding the starting site of the MPS to build a correct dense-form operator.
        START_SITE = min(constraint_string.flat())

        # Preparing the dense-form operator.
        identities_l = [IDENTITY for _ in range(START_SITE)]
        identities_r = [
            IDENTITY for _ in range(NUM_BITS - len(constraint_mpo) - START_SITE)
        ]
        full_mpo = identities_l + constraint_mpo + identities_r
        mpo_dense = mpo_to_matrix(full_mpo, interlace=False, group=True)

        # Doing the contraction in dense form.
        state_dense = mpo_dense @ state_dense

    # Tolerance under which we round tensor elements to zero.
    TOL = 1e-12
    mps_dense = state.dense(flatten=True)
    mps_dense[np.abs(mps_dense) < TOL] = 0

    # Retreiving codewords.
    cwords = get_codewords(example_code)
    cwords_to_compare_mps = np.flatnonzero(mps_dense)
    cwords_to_compare_dense = np.flatnonzero(state_dense)

    print()
    print("Codewords from the generator matrix:")
    print(cwords)
    print("Codewords from the dense-form simulation:")
    print(cwords_to_compare_mps)
    print("Codewords from the MPS-form simulation:")
    print(cwords_to_compare_dense)
    print("")
    print(
        "All lists of codewords match:",
        np.logical_and(
            np.array_equal(cwords, cwords_to_compare_mps),
            np.array_equal(cwords_to_compare_mps, cwords_to_compare_dense),
        ),
    )
    print(
        "__________________________________________________________________________________________"
    )

    print("")
    print("Retreiving a perturbed codeword:")
    print("")

    # Defining the parameters of a classical LDPC code.
    NUM_BITS, NUM_CHECKS = 24, 18
    CHECK_DEGREE, BIT_DEGREE = 4, 3
    if NUM_BITS / NUM_CHECKS != CHECK_DEGREE / BIT_DEGREE:
        raise ValueError("The Tanner graph of the code must be bipartite.")

    # Defining the bias channel parameter and the error probability.
    PROB_ERROR = 0.15
    PROB_CHANNEL = PROB_ERROR

    # Maximum bond dimension for contractor/DMRG.
    CHI_MAX_CONTRACTOR = 1e4
    CHI_MAX_DMRG = 1e4
    # Number of DMRG sweeps.
    NUM_RUNS = 1

    # Constructing the code as a qecstruct object.
    example_code = qec.random_regular_code(
        NUM_BITS, NUM_CHECKS, BIT_DEGREE, CHECK_DEGREE, qec.Rng(SEED)
    )

    # Getting the sites where each string of constraints should be applied.
    code_constraint_sites = get_constraint_sites(example_code)

    # Building an initial and a perturbed codeword.
    INITIAL_CODEWORD, PERTURBED_CODEWORD = prepare_codewords(
        example_code, PROB_ERROR, error_model=qec.BinarySymmetricChannel, seed=SEED
    )
    print("The initial codeword is", INITIAL_CODEWORD)
    print("The perturbed codeword is", PERTURBED_CODEWORD)
    print("")

    # Building the corresponding matrix product states.
    initial_codeword_state = create_custom_product_state(
        INITIAL_CODEWORD
    ).right_canonical()
    perturbed_codeword_state = create_custom_product_state(
        PERTURBED_CODEWORD
    ).right_canonical()

    # Passing the perturbed codeword state through the bias channel.
    perturbed_codeword_state = apply_bias_channel(
        perturbed_codeword_state,
        codeword_string=PERTURBED_CODEWORD,
        prob_channel=PROB_CHANNEL,
    )

    print("Applying constraints:")
    print("")
    # Applying the parity constraints defined by the code.
    perturbed_codeword_state = apply_parity_constraints(
        perturbed_codeword_state,
        code_constraint_sites,
        tensors,
        chi_max=CHI_MAX_CONTRACTOR,
        renormalise=True,
        strategy="naive",
        silent=False,
    )

    print("Decoding:")
    print("")
    # Decoding the perturbed codeword.
    dmrg_container, success = decode(
        message=perturbed_codeword_state,
        codeword=initial_codeword_state,
        code=example_code,
        num_runs=NUM_RUNS,
        chi_max_dmrg=CHI_MAX_DMRG,
        cut=1e-10,
        silent=False,
    )
    print(
        "The overlap of the density MPO main component and the initial codeword state: ",
        success,
    )
    print(
        "__________________________________________________________________________________________"
    )
