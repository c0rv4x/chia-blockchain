from __future__ import annotations

import hashlib
import struct
from dataclasses import astuple, dataclass
from random import Random
from typing import Dict, Generic, List, Set, Tuple, Type, TypeVar, final

import pytest

# TODO: update after resolution in https://github.com/pytest-dev/pytest/issues/7469
from _pytest.fixtures import SubRequest

from chia._tests.util.misc import DataCase, Marks, datacases
from chia.data_layer.util.merkle_blob import (
    InvalidIndexError,
    KVId,
    MerkleBlob,
    NodeMetadata,
    NodeType,
    RawInternalMerkleNode,
    RawLeafMerkleNode,
    RawMerkleNodeProtocol,
    TreeIndex,
    data_size,
    metadata_size,
    null_parent,
    pack_raw_node,
    raw_node_classes,
    raw_node_type_to_class,
    spacing,
    unpack_raw_node,
)


@pytest.fixture(
    name="raw_node_class",
    scope="session",
    params=raw_node_classes,
    ids=[cls.type.name for cls in raw_node_classes],
)
def raw_node_class_fixture(request: SubRequest) -> RawMerkleNodeProtocol:
    # https://github.com/pytest-dev/pytest/issues/8763
    return request.param  # type: ignore[no-any-return]


class_to_structs: Dict[Type[object], struct.Struct] = {
    NodeMetadata: NodeMetadata.struct,
    **{cls: cls.struct for cls in raw_node_classes},
}


@pytest.fixture(
    name="class_struct",
    scope="session",
    params=class_to_structs.values(),
    ids=[cls.__name__ for cls in class_to_structs.keys()],
)
def class_struct_fixture(request: SubRequest) -> RawMerkleNodeProtocol:
    # https://github.com/pytest-dev/pytest/issues/8763
    return request.param  # type: ignore[no-any-return]


def test_raw_node_class_types_are_unique() -> None:
    assert len(raw_node_type_to_class) == len(raw_node_classes)


def test_metadata_size_not_changed() -> None:
    assert metadata_size == 2


def test_data_size_not_changed() -> None:
    assert data_size == 52


def test_raw_node_struct_sizes(raw_node_class: RawMerkleNodeProtocol) -> None:
    assert raw_node_class.struct.size == data_size


def test_all_big_endian(class_struct: struct.Struct) -> None:
    assert class_struct.format.startswith(">")


# TODO: check all struct types against attribute types

RawMerkleNodeT = TypeVar("RawMerkleNodeT", bound=RawMerkleNodeProtocol)


reference_blob = bytes(range(data_size))


@final
@dataclass
class RawNodeFromBlobCase(Generic[RawMerkleNodeT]):
    raw: RawMerkleNodeT
    blob_to_unpack: bytes = reference_blob
    packed_blob_reference: bytes = reference_blob

    marks: Marks = ()

    @property
    def id(self) -> str:
        return self.raw.type.name


reference_raw_nodes: List[DataCase] = [
    RawNodeFromBlobCase(
        raw=RawInternalMerkleNode(
            parent=TreeIndex(0x00010203),
            left=TreeIndex(0x04050607),
            right=TreeIndex(0x08090A0B),
            hash=bytes(range(12, data_size)),
            index=TreeIndex(0),
        ),
    ),
    RawNodeFromBlobCase(
        raw=RawLeafMerkleNode(
            parent=TreeIndex(0x00010203),
            key=KVId(0x0405060708090A0B),
            value=KVId(0x0405060708090A1B),
            hash=bytes(range(12, data_size)),
            index=TreeIndex(0),
        ),
    ),
]


@datacases(*reference_raw_nodes)
def test_raw_node_from_blob(case: RawNodeFromBlobCase[RawMerkleNodeProtocol]) -> None:
    node = unpack_raw_node(
        index=TreeIndex(0),
        metadata=NodeMetadata(type=case.raw.type, dirty=False),
        data=case.blob_to_unpack,
    )
    assert node == case.raw


@datacases(*reference_raw_nodes)
def test_raw_node_to_blob(case: RawNodeFromBlobCase[RawMerkleNodeProtocol]) -> None:
    blob = pack_raw_node(case.raw)
    assert blob == case.packed_blob_reference


def test_merkle_blob_one_leaf_loads() -> None:
    # TODO: need to persist reference data
    leaf = RawLeafMerkleNode(
        parent=null_parent,
        key=KVId(0x0405060708090A0B),
        value=KVId(0x0405060708090A1B),
        hash=bytes(range(12, data_size)),
        index=TreeIndex(0),
    )
    blob = bytearray(NodeMetadata(type=NodeType.leaf, dirty=False).pack() + pack_raw_node(leaf))

    merkle_blob = MerkleBlob(blob=blob)
    assert merkle_blob.get_raw_node(TreeIndex(0)) == leaf


def test_merkle_blob_two_leafs_loads() -> None:
    # TODO: break this test down into some reusable data and multiple tests
    # TODO: need to persist reference data
    root = RawInternalMerkleNode(
        parent=null_parent,
        left=TreeIndex(1),
        right=TreeIndex(2),
        hash=bytes(range(12, data_size)),
        index=TreeIndex(0),
    )
    left_leaf = RawLeafMerkleNode(
        parent=TreeIndex(0),
        key=KVId(0x0405060708090A0B),
        value=KVId(0x0405060708090A1B),
        hash=bytes(range(12, data_size)),
        index=TreeIndex(1),
    )
    right_leaf = RawLeafMerkleNode(
        parent=TreeIndex(0),
        key=KVId(0x1415161718191A1B),
        value=KVId(0x1415161718191A2B),
        hash=bytes(range(12, data_size)),
        index=TreeIndex(2),
    )
    blob = bytearray()
    blob.extend(NodeMetadata(type=NodeType.internal, dirty=True).pack() + pack_raw_node(root))
    blob.extend(NodeMetadata(type=NodeType.leaf, dirty=False).pack() + pack_raw_node(left_leaf))
    blob.extend(NodeMetadata(type=NodeType.leaf, dirty=False).pack() + pack_raw_node(right_leaf))

    merkle_blob = MerkleBlob(blob=blob)
    assert merkle_blob.get_raw_node(TreeIndex(0)) == root
    assert merkle_blob.get_raw_node(root.left) == left_leaf
    assert merkle_blob.get_raw_node(root.right) == right_leaf
    assert merkle_blob.get_raw_node(left_leaf.parent) == root
    assert merkle_blob.get_raw_node(right_leaf.parent) == root

    assert merkle_blob.get_lineage(TreeIndex(0)) == [root]
    assert merkle_blob.get_lineage(root.left) == [left_leaf, root]


def generate_kvid(seed: int) -> Tuple[KVId, KVId]:
    kv_ids = []

    for offset in range(2):
        seed_bytes = (2 * seed + offset).to_bytes(8, byteorder="big")
        hash_obj = hashlib.sha256(seed_bytes)
        hash_int = int.from_bytes(hash_obj.digest()[:8], byteorder="big")
        kv_ids.append(hash_int)

    return tuple(kv_ids)


def generate_hash(seed: int) -> bytes:
    seed_bytes = seed.to_bytes(8, byteorder="big")
    hash_obj = hashlib.sha256(seed_bytes)
    return hash_obj.digest()


def test_insert_delete_loads_all_keys() -> None:
    merkle_blob = MerkleBlob(blob=bytearray())
    num_keys = 200000
    extra_keys = 100000
    max_height = 25
    keys_values: Dict[KVId, KVId] = {}

    random = Random()
    random.seed(100, version=2)
    expected_num_entries = 0
    current_num_entries = 0

    for seed in range(num_keys):
        [op_type] = random.choices(["insert", "delete"], [0.7, 0.3], k=1)
        if op_type == "delete" and len(keys_values) > 0:
            key = random.choice(list(keys_values.keys()))
            del keys_values[key]
            merkle_blob.delete(key)
            if current_num_entries == 1:
                current_num_entries = 0
                expected_num_entries = 0
            else:
                current_num_entries -= 2
        else:
            key, value = generate_kvid(seed)
            hash = generate_hash(seed)
            merkle_blob.insert(key, value, hash)
            key_index = merkle_blob.key_to_index[key]
            lineage = merkle_blob.get_lineage(TreeIndex(key_index))
            assert len(lineage) <= max_height
            keys_values[key] = value
            if current_num_entries == 0:
                current_num_entries = 1
            else:
                current_num_entries += 2

        expected_num_entries = max(expected_num_entries, current_num_entries)
        assert len(merkle_blob.blob) // spacing == expected_num_entries

    assert merkle_blob.get_keys_values() == keys_values

    merkle_blob_2 = MerkleBlob(blob=merkle_blob.blob)
    for seed in range(num_keys, num_keys + extra_keys):
        key, value = generate_kvid(seed)
        hash = generate_hash(seed)
        merkle_blob_2.upsert(key, value, hash)
        key_index = merkle_blob_2.key_to_index[key]
        lineage = merkle_blob_2.get_lineage(TreeIndex(key_index))
        assert len(lineage) <= max_height
        keys_values[key] = value
    assert merkle_blob.get_keys_values() == keys_values


def test_small_insert_deletes() -> None:
    merkle_blob = MerkleBlob(blob=bytearray())
    num_repeats = 100
    max_inserts = 25
    seed = 0

    random = Random()
    random.seed(100, version=2)

    for repeats in range(num_repeats):
        for num_inserts in range(1, max_inserts):
            keys_values: Dict[KVId, KVId] = {}
            for inserts in range(num_inserts):
                seed += 1
                key, value = generate_kvid(seed)
                hash = generate_hash(seed)
                merkle_blob.insert(key, value, hash)
                keys_values[key] = value

            delete_order = list(keys_values.keys())
            random.shuffle(delete_order)
            remaining_keys_values = set(keys_values.keys())
            for kv_id in delete_order:
                merkle_blob.delete(kv_id)
                remaining_keys_values.remove(kv_id)
                assert set(merkle_blob.get_keys_values().keys()) == remaining_keys_values
            assert not remaining_keys_values


def test_proof_of_inclusion_merkle_blob() -> None:
    num_repeats = 10
    num_inserts = 1000
    num_deletes = 100
    seed = 0

    random = Random()
    random.seed(100, version=2)

    merkle_blob = MerkleBlob(blob=bytearray())
    keys_values: Dict[KVId, KVId] = {}

    for repeats in range(num_repeats):
        kv_ids: List[Tuple[KVId, KVId]] = []
        hashes: List[bytes] = []
        for _ in range(num_inserts):
            seed += 1
            key, value = generate_kvid(seed)
            kv_ids.append((key, value))
            hashes.append(generate_hash(seed))
            keys_values[key] = value

        merkle_blob.batch_insert(kv_ids, hashes)
        merkle_blob.calculate_lazy_hashes()

        for kv_id in keys_values.keys():
            proof_of_inclusion = merkle_blob.get_proof_of_inclusion(kv_id)
            assert proof_of_inclusion.valid()

        delete_ordering = list(keys_values.keys())
        random.shuffle(delete_ordering)
        delete_ordering = delete_ordering[:num_deletes]
        for kv_id in delete_ordering:
            merkle_blob.delete(kv_id)
            del keys_values[kv_id]

        for kv_id in delete_ordering:
            with pytest.raises(Exception, match=f"Key {kv_id} not present in the store"):
                merkle_blob.get_proof_of_inclusion(kv_id)

        new_keys_values: Dict[KVId, KVId] = {}
        for old_kv in keys_values.keys():
            seed += 1
            _, value = generate_kvid(seed)
            hash = generate_hash(seed)
            merkle_blob.upsert(old_kv, value, hash)
            new_keys_values[old_kv] = value
        merkle_blob.calculate_lazy_hashes()

        keys_values = new_keys_values
        for kv_id in keys_values:
            proof_of_inclusion = merkle_blob.get_proof_of_inclusion(kv_id)
            assert proof_of_inclusion.valid()


@pytest.mark.parametrize(argnames="index", argvalues=[TreeIndex(-1), TreeIndex(1), TreeIndex(null_parent)])
def test_get_raw_node_raises_for_invalid_indexes(index: TreeIndex) -> None:
    merkle_blob = MerkleBlob(blob=bytearray())
    merkle_blob.insert(KVId(0x1415161718191A1B), KVId(0x1415161718191A1B), bytes(range(12, data_size)))

    with pytest.raises(InvalidIndexError):
        merkle_blob.get_raw_node(index)
        merkle_blob.get_metadata(index)


@pytest.mark.parametrize(argnames="cls", argvalues=raw_node_classes)
def test_as_tuple_matches_dataclasses_astuple(cls: Type[RawMerkleNodeProtocol], seeded_random: Random) -> None:
    raw_bytes = bytes(seeded_random.getrandbits(8) for _ in range(cls.struct.size))
    raw_node = cls(*cls.struct.unpack(raw_bytes), index=TreeIndex(seeded_random.randrange(1_000_000)))
    # hacky [:-1] to exclude the index
    # TODO: try again to indicate that the RawMerkleNodeProtocol requires the dataclass interface
    assert raw_node.as_tuple() == astuple(raw_node)[:-1]  # type: ignore[call-overload]