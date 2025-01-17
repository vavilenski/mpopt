"""Tests for the explicit MPS construction."""

from functools import reduce
from typing import Iterable
import pytest
import numpy as np
from opt_einsum import contract

from mpopt.mps.explicit import ExplicitMPS
from mpopt.mps.utils import (
    create_state_vector,
    is_canonical,
    mps_from_dense,
    create_simple_product_state,
    inner_product,
    find_orth_centre,
)


def test_explicit_init():
    """Tests for the :method:`__init__`, :method:`__len__` and
    :method:`__iter__` methods of the :class:`ExplicitMPS` class.
    """

    for _ in range(10):

        num_sites = np.random.randint(4, 9)

        product_mps = create_simple_product_state(
            num_sites=num_sites, which="0", phys_dim=2
        )
        product_tensor = np.array([1.0, 0.0]).reshape((1, 2, 1))
        product_tensors = [product_tensor for _ in range(num_sites)]

        assert np.isclose(product_mps.tensors, product_tensors).all()
        assert np.isclose(
            product_mps.singular_values, [[1.0] for _ in range(num_sites + 1)]
        ).all()
        assert np.isclose(
            product_mps.bond_dimensions, [1 for _ in range(product_mps.num_bonds)]
        ).all()
        assert np.isclose(
            product_mps.phys_dimensions, [2 for _ in range(product_mps.num_sites)]
        ).all()

        psi = create_state_vector(num_sites)
        tolerance = 1e-12
        chi_max = 1e4
        mps = mps_from_dense(psi, form="Explicit")

        assert np.isclose(mps.num_sites, num_sites).all()
        assert np.isclose(mps.num_bonds, num_sites - 1).all()
        assert np.isclose(mps.num_singval_mat, len(mps.singular_values)).all()
        assert mps.dtype == np.dtype(np.complex128)
        assert np.isclose(mps.tolerance, tolerance).all()
        assert np.isclose(mps.chi_max, chi_max).all()
        assert np.isclose(len(mps), mps.num_sites).all()
        assert isinstance(iter(mps), zip)

        with pytest.raises(ValueError):
            ExplicitMPS(
                tensors=product_tensors,
                singular_values=product_mps.singular_values + [1.0],
            )

        with pytest.raises(ValueError):
            mps.tensors[0] = np.expand_dims(mps.tensors[0], 0)
            ExplicitMPS(
                tensors=mps.tensors,
                singular_values=mps.singular_values,
            )

        with pytest.raises(ValueError):
            ExplicitMPS(
                tensors=mps.tensors, singular_values=mps.singular_values + [0.0]
            )


def test_explicit_copy():
    """Test for the :method:`copy` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")
        mps_copied = mps.copy()

        for tensor, tensor_copy in zip(mps.tensors, mps_copied.tensors):
            assert np.isclose(tensor, tensor_copy).all()
        assert np.isclose(mps.num_sites, mps_copied.num_sites).all()
        assert np.isclose(mps.num_bonds, mps_copied.num_bonds).all()
        assert np.isclose(mps.bond_dimensions, mps_copied.bond_dimensions).all()
        assert np.isclose(mps.phys_dimensions, mps_copied.phys_dimensions).all()
        for sing_vals, sing_vals_copied in zip(
            mps.singular_values, mps_copied.singular_values
        ):
            assert np.isclose(sing_vals, sing_vals_copied).all()
        assert np.isclose(mps.num_singval_mat, mps_copied.num_singval_mat).all()
        assert mps.dtype == mps_copied.dtype
        assert np.isclose(mps.tolerance, mps_copied.tolerance).all()
        assert np.isclose(mps.chi_max, mps_copied.chi_max).all()
        assert np.isclose(len(mps), len(mps_copied)).all()


def test_explicit_reverse():
    """Test for the :method:`reverse` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")
        mps_reversed = mps.reverse()

        for tensor, reversed_tensor in zip(reversed(mps.tensors), mps_reversed.tensors):
            assert np.isclose(tensor, np.transpose(reversed_tensor)).all()
        assert np.isclose(mps_reversed.num_sites, mps.num_sites).all()
        assert np.isclose(mps_reversed.num_bonds, mps.num_bonds).all()
        assert np.isclose(mps_reversed.bond_dimensions, mps.bond_dimensions).all()
        assert np.isclose(mps_reversed.phys_dimensions, mps.phys_dimensions).all()
        for sing_vals, sing_vals_reversed in zip(
            mps_reversed.singular_values, reversed(mps.singular_values)
        ):
            assert np.isclose(sing_vals, sing_vals_reversed).all()
        assert mps_reversed.dtype == mps.dtype
        assert np.isclose(mps_reversed.tolerance, mps.tolerance).all()
        assert np.isclose(mps_reversed.chi_max, mps.chi_max).all()
        assert np.isclose(len(mps_reversed), len(mps)).all()


def test_explicit_conjugate():
    """Test for the :method:`conjugate` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")
        mps_conjugated = mps.conjugate()

        for tensor, conjugated_tensor in zip(mps.tensors, mps_conjugated.tensors):
            assert np.isclose(tensor, np.conjugate(conjugated_tensor)).all()
        assert np.isclose(mps.num_sites, mps_conjugated.num_sites).all()
        assert np.isclose(mps.num_bonds, mps_conjugated.num_bonds).all()
        assert np.isclose(mps.bond_dimensions, mps_conjugated.bond_dimensions).all()
        assert np.isclose(mps.phys_dimensions, mps_conjugated.phys_dimensions).all()

        for singular_values, conjugated_singular_values in zip(
            mps.singular_values, mps_conjugated.singular_values
        ):
            assert np.isclose(
                singular_values, np.conjugate(conjugated_singular_values)
            ).all()
        assert np.isclose(mps.num_singval_mat, mps_conjugated.num_singval_mat).all()
        assert mps.dtype == mps_conjugated.dtype
        assert np.isclose(mps.tolerance, mps_conjugated.tolerance).all()
        assert np.isclose(mps.chi_max, mps_conjugated.chi_max).all()
        assert np.isclose(len(mps), len(mps_conjugated)).all()


def test_explicit_single_site_left_iso():
    """Test for the :method:`single_site_left_iso` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        for site in range(num_sites):
            isometry = mps.single_site_left_iso(site)

            to_be_identity = contract(
                "ijk, ijl -> kl", isometry, np.conjugate(isometry), optimize=[(0, 1)]
            )

            assert np.isclose(
                np.linalg.norm(to_be_identity - np.identity(to_be_identity.shape[0])), 0
            )


def test_explicit_single_site_right_iso():
    """Test for the :method:`single_site_right_iso` method of the :method:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        for site in range(num_sites):

            isometry = mps.single_site_right_iso(site)

            to_be_identity = contract(
                "ijk, ljk -> il", isometry, np.conjugate(isometry), optimize=[(0, 1)]
            )

            assert np.isclose(
                np.linalg.norm(to_be_identity - np.identity(to_be_identity.shape[0])), 0
            )


def test_explicit_single_left_iso_iter():
    """Test for the :method:`single_site_left_iso_iter` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        assert isinstance(mps.single_site_left_iso_iter(), Iterable)


def test_explicit_single_right_iso_iter():
    """Test for the :method:`single_site_right_iso_iter` method of :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        assert isinstance(mps.single_site_right_iso_iter(), Iterable)


def test_explicit_two_site_left_iso():
    """Test for the :method:`two_site_left_iso` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        with pytest.raises(ValueError):
            mps.two_site_left_iso(num_sites)

        for site in range(num_sites - 1):
            two_site_left_iso = mps.two_site_left_iso(site)

            to_be_identity = contract(
                "ijkl, ijkm -> lm",
                two_site_left_iso,
                np.conjugate(two_site_left_iso),
                optimize=[(0, 1)],
            )

            assert np.isclose(
                np.linalg.norm(to_be_identity - np.identity(to_be_identity.shape[-1])),
                0,
            )


def test_explicit_two_site_right_iso():
    """Test for the :method:`two_site_right_iso` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        with pytest.raises(ValueError):
            mps.two_site_right_iso(num_sites)

        for site in range(num_sites - 1):
            two_site_right_iso = mps.two_site_right_iso(site)

            to_be_identity = contract(
                "ijkl, mjkl -> im",
                two_site_right_iso,
                np.conjugate(two_site_right_iso),
                optimize=[(0, 1)],
            )

            assert np.isclose(
                np.linalg.norm(to_be_identity - np.identity(to_be_identity.shape[-1])),
                0,
            )


def test_explicit_two_site_iter():
    """Test for the :method:`two_site_right_iso_iter` and :method:`two_site_right_iso_iter`
    methods of the :class:`ExplicitMPS` class.
    """

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        assert isinstance(mps.two_site_right_iso_iter(), Iterable)
        assert isinstance(mps.two_site_left_iso_iter(), Iterable)


def test_explicit_dense():
    """Test for the :method:`dense` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        assert np.isclose(psi, mps.dense(flatten=True)).all()


def test_explicit_density_mpo():
    """Test for the :method:`density_mpo` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        density_mpo = mps.density_mpo()

        # Juggle the dimensions around to apply the `reduce` function later,
        # which is used to create a density mpo to compare the method against.
        for i in range(num_sites):
            density_mpo[i] = density_mpo[i].transpose((0, 3, 2, 1))

        density_matrix_mpo = reduce(
            lambda a, b: np.tensordot(a, b, (-1, 0)), density_mpo
        )

        # Get rid of ghost dimensions of the MPO.
        density_matrix_mpo = density_matrix_mpo.squeeze()
        # Reshaping to the right order of indices.
        correct_order = list(range(0, 2 * num_sites, 2)) + list(
            range(1, 2 * num_sites, 2)
        )
        print(correct_order)
        print(density_matrix_mpo.shape)
        density_matrix_mpo = density_matrix_mpo.transpose(correct_order)
        # Reshaping to the matrix form.
        density_matrix_mpo = density_matrix_mpo.reshape(
            (2**num_sites, 2**num_sites)
        )

        # Original density matrix.
        density_matrix = np.tensordot(psi, np.conjugate(psi), 0)

        assert np.isclose(np.trace(density_matrix), 1)
        assert np.isclose(
            np.linalg.norm(density_matrix - np.conjugate(density_matrix).T), 0
        )

        assert np.isclose(np.trace(density_matrix_mpo), 1)
        assert np.isclose(
            np.linalg.norm(density_matrix_mpo - np.conjugate(density_matrix_mpo).T),
            0,
        )


def test_explicit_entanglement_entropy():
    """Test for the :method:`entanglement_entropy` method of the :class:`ExplicitMPS` class."""

    num_sites = 4

    psi_two_body_dimer = 1 / np.sqrt(2) * np.array([0, -1, 1, 0], dtype=np.float64)
    psi_many_body_dimer = reduce(np.kron, [psi_two_body_dimer] * num_sites)

    mps_dimer = mps_from_dense(psi_many_body_dimer, form="Explicit")

    entropy_list = np.array(mps_dimer.entanglement_entropy())

    correct_entropy_list = np.array([0, np.log(2), 0, np.log(2), 0, np.log(2), 0])

    zeros = entropy_list - correct_entropy_list

    assert np.allclose(np.linalg.norm(zeros), 0)


def test_explicit_right_canonical():
    """Test for the :method:`right_canonical` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        mps_right = mps.right_canonical()

        assert is_canonical(mps_right)
        assert np.isclose(abs(inner_product(mps_right, mps_right)), 1)
        assert len(find_orth_centre(mps_right)) == 1

        for i in range(num_sites):
            assert mps.tensors[i].shape == mps_right.tensors[i].shape

        for i, _ in enumerate(mps_right.tensors):

            to_be_identity_right = contract(
                "ijk, ljk -> il",
                mps_right.tensors[i],
                np.conjugate(mps_right.tensors[i]),
                optimize=[(0, 1)],
            )

            identity_right = np.identity(
                to_be_identity_right.shape[0], dtype=np.float64
            )

            assert np.isclose(np.linalg.norm(to_be_identity_right - identity_right), 0)


def test_explicit_left_canonical():
    """Test for the :method:`left_canonical` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        mps_left = mps.left_canonical()

        assert is_canonical(mps_left)
        assert np.isclose(abs(inner_product(mps_left, mps_left)), 1)
        assert len(find_orth_centre(mps_left)) == 1

        for i in range(num_sites):
            assert mps.tensors[i].shape == mps_left.tensors[i].shape

        for i, tensor in enumerate(mps_left.tensors):

            to_be_identity_left = contract(
                "ijk, ijl -> kl",
                tensor,
                np.conjugate(tensor),
                optimize=[(0, 1)],
            )

            identity_left = np.identity(to_be_identity_left.shape[0], dtype=np.float64)

            assert np.isclose(np.linalg.norm(to_be_identity_left - identity_left), 0)


def test_explicit_mixed_canonical():
    """Test for the :method:`mixed_canonical` method of the :class:`ExplicitMPS` class."""

    num_sites = np.random.randint(4, 9)

    for _ in range(10):

        psi = create_state_vector(num_sites)
        mps = mps_from_dense(psi, form="Explicit")

        orth_centre_index = np.random.randint(num_sites)
        mps_mixed = mps.mixed_canonical(orth_centre_index)

        for i in range(num_sites):
            assert mps.tensors[i].shape == mps_mixed.tensors[i].shape
        assert is_canonical(mps_mixed)
        assert np.isclose(abs(inner_product(mps_mixed, mps_mixed)), 1)
        assert find_orth_centre(mps_mixed) == [orth_centre_index]
