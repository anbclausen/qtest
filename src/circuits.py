from itertools import takewhile
import json
import math
import re

import sys
from typing import Any
from qiskit import QuantumCircuit, QuantumRegister, qasm2
from qiskit.circuit import Qubit, Instruction, CircuitInstruction
from qiskit.dagcircuit import DAGOpNode
from qiskit.converters import circuit_to_dag, dag_to_circuit


class LogicalQubit:
    def __init__(self, id: int):
        self.id = id

    def __str__(self):
        return f"q_{self.id}"

    def __eq__(self, other):
        if isinstance(other, LogicalQubit):
            return self.id == other.id
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.id)


class PhysicalQubit:
    def __init__(self, id: int):
        self.id = id

    def __str__(self):
        return f"p_{self.id}"

    def __eq__(self, other):
        if isinstance(other, PhysicalQubit):
            return self.id == other.id
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.id)


type InitialMapping = dict[LogicalQubit, PhysicalQubit]


def remove_all_non_cx_gates(circuit: QuantumCircuit) -> QuantumCircuit:
    """
    Remove all non-CX gates from the circuit.
    """
    num_qubits = circuit.num_qubits
    qubit_name = circuit.qregs[0].name
    new_circuit = QuantumCircuit(QuantumRegister(num_qubits, qubit_name))
    for instr in circuit.data:
        if instr[0].name == "cx":
            new_circuit.append(instr[0], instr[1])

    return new_circuit


def count_swaps(circuit: QuantumCircuit):
    """
    Counts SWAP gates in a circuit.
    """
    swaps = 0
    for instr in circuit.data:
        if instr[0].name.startswith("swap"):
            swaps += 1

    return swaps


def with_swaps_as_cnots(circuit: QuantumCircuit, register_name: str):
    """
    Replaces all SWAP gates with CNOT gates.
    """
    new_circuit = QuantumCircuit(QuantumRegister(circuit.num_qubits, register_name))
    for instr in circuit.data:
        if instr[0].name.startswith("swap"):
            new_circuit.cx(instr[1][0]._index, instr[1][1]._index)
            new_circuit.cx(instr[1][1]._index, instr[1][0]._index)
            new_circuit.cx(instr[1][0]._index, instr[1][1]._index)
        else:
            new_circuit.append(instr[0], instr[1])

    return new_circuit


def get_stats(circuit: QuantumCircuit):
    """
    Get circuit statistics.
    """
    cx_circuit = with_swaps_as_cnots(circuit, "q")
    cx_circuit_only = remove_all_non_cx_gates(cx_circuit)
    depth = cx_circuit.depth()
    cx_depth = cx_circuit_only.depth()
    swap_count = count_swaps(circuit)
    return depth, cx_depth, swap_count


def str_to_float_str(input: str) -> str:
    result = str(float(eval(input.replace("pi", str(math.pi)))))
    return result


def parse_olsq2_circuit(
    path: str, platform_depth: int, gate_lines: list[str]
) -> tuple[QuantumCircuit, InitialMapping]:
    """
    Parse the circuit from the output file of OLSQ2.
    """
    with open(path, "r") as f:
        data: dict[str, Any] = json.load(f)

    raw_initial_mapping: list[int] = data["initial_mapping"]
    initial_mapping = {
        LogicalQubit(i): PhysicalQubit(raw_initial_mapping[i])
        for i in range(len(raw_initial_mapping))
    }

    gates = []
    for line in gate_lines:
        if line.startswith("SWAP"):
            parts = line.split(" ")
            qubits = list(map(int, parts[4][1:-1].split(",")))
            time = int(parts[7])
            gates.append(("swap", qubits, time, sys.maxsize))
        else:
            parts = re.search(
                r"Gate (\d+): (\w+)(\(.+\))? (.+) on (qubits|qubit) (.+) at time (\d+)",
                line,
            )
            if parts is None:
                raise ValueError(f"Could not parse gate line: {line}")
            gate_id = int(parts.group(1))
            gate_name = parts.group(2)
            extra_name = parts.group(3)
            if extra_name:
                paren_removed = extra_name[1:-1]
                if "," in paren_removed:
                    args = [str_to_float_str(arg) for arg in paren_removed.split(",")]
                    gate_name = f"{gate_name}_{'_'.join(args)}"
                elif "pi" in paren_removed:
                    gate_name = f"{gate_name}_{str_to_float_str(paren_removed)}"
                else:
                    gate_name = f"{gate_name}_{paren_removed}"
            qubits = list(map(int, parts.group(6).split(" and ")))
            time = int(parts.group(7))
            gates.append((gate_name, qubits, time, gate_id))

    gates = sorted(gates, key=lambda x: (x[2], x[3]))
    register = QuantumRegister(platform_depth, "q")
    result_circuit = QuantumCircuit(register)
    for gate_name, qubits, time, gate_id in gates:
        match gate_name:
            case "swap":
                result_circuit.swap(qubits[0], qubits[1])
            case "cx":
                result_circuit.cx(qubits[0], qubits[1])
            case "x":
                result_circuit.x(qubits[0])
            case "h":
                result_circuit.h(qubits[0])
            case "t":
                result_circuit.t(qubits[0])
            case "tdg":
                result_circuit.tdg(qubits[0])
            case "s":
                result_circuit.s(qubits[0])
            case "sdg":
                result_circuit.sdg(qubits[0])
            case "y":
                result_circuit.y(qubits[0])
            case "z":
                result_circuit.z(qubits[0])
            case name if name.startswith("rx"):
                angle = float(name.split("_")[1])
                result_circuit.rx(angle, qubits[0])
            case name if name.startswith("rz"):
                angle = float(name.split("_")[1])
                result_circuit.rz(angle, qubits[0])
            case name if name.startswith("u_"):
                theta = float(name.split("_")[1])
                phi = float(name.split("_")[2])
                lam = float(name.split("_")[3])
                result_circuit.u(theta, phi, lam, qubits[0])
            case name if name.startswith("u3"):
                theta = float(name.split("_")[1])
                phi = float(name.split("_")[2])
                lam = float(name.split("_")[3])
                instr = CircuitInstruction(
                    operation=Instruction(
                        name="u3",
                        num_qubits=1,
                        num_clbits=0,
                        params=[theta, phi, lam],
                    ),
                    qubits=(Qubit(register, qubits[0]),),
                    clbits=(),
                )
                result_circuit.append(instr)
            case name if name.startswith("u2"):
                phi = float(name.split("_")[1])
                lam = float(name.split("_")[2])
                instr = CircuitInstruction(
                    operation=Instruction(
                        name="u2",
                        num_qubits=1,
                        num_clbits=0,
                        params=[phi, lam],
                    ),
                    qubits=(Qubit(register, qubits[0]),),
                    clbits=(),
                )
                result_circuit.append(instr)
            case name if name.startswith("u1"):
                lam = float(name.split("_")[1])
                instr = CircuitInstruction(
                    operation=Instruction(
                        name="u1",
                        num_qubits=1,
                        num_clbits=0,
                        params=[lam],
                    ),
                    qubits=(Qubit(register, qubits[0]),),
                    clbits=(),
                )
                result_circuit.append(instr)
            case _:
                raise ValueError(
                    f"Unknown unary gate: '{gate_name}'... Perhaps you should add it to the match statement?"
                )

    return result_circuit, initial_mapping


def save_circuit(
    circuit: QuantumCircuit,
    file_path: str,
):
    register = QuantumRegister(circuit.num_qubits, "q")
    output_circuit = QuantumCircuit(register)
    for instr in circuit.data:
        new_instr = instr.replace(
            qubits=[Qubit(register, q._index) for q in instr.qubits]
        )
        output_circuit.append(new_instr)
    circuit_file = open(file_path, "w")
    qasm2.dump(output_circuit, circuit_file)
    circuit_file.close()


def gate_line_dependency_mapping(
    circuit: QuantumCircuit,
) -> dict[int, tuple[str, list[int]]]:
    """
    Returns a mapping of gate index to the name of the gate and the qubits it acts on.

    Example
    -------
    Given circuit:
         ┌───┐
    q_0: ┤ X ├──■──
         ├───┤┌─┴─┐
    q_1: ┤ X ├┤ X ├
         └───┘├───┤
    q_2: ──■──┤ X ├
         ┌─┴─┐└───┘
    q_3: ┤ X ├─────
         └───┘

    The mapping would be:
    `{0: ('x', [0]), 1: ('x', [1]), 2: ('cx', [2, 3]), 3: ('cx', [0, 1]), 4: ('x', [2])}`
    """
    circuit_data = list(circuit.data)

    mapping = {}
    for i, instr in enumerate(circuit_data):
        name = instr.operation.name
        input_idxs = [qubit._index for qubit in instr.qubits]
        if name is None:
            raise ValueError(f"Gate at index {i} has no name.")

        if len(input_idxs) > 1 and name != "cx" and name != "swap":
            raise ValueError(
                f"Gate at index {i} is not a CX or SWAP but has multiple inputs. qt can not handle multiple input gates other than CX or SWAP."
            )

        if any(idx is None for idx in input_idxs):
            raise ValueError(f"Gate at index {i} has an input with no index.")

        if name == "rx" or name == "rz":
            angle = instr.operation.params[0]
            name = f"{name}_{angle}"
        if name.startswith("u"):
            if name == "u" or name == "u3":
                theta = instr.operation.params[0]
                phi = instr.operation.params[1]
                lam = instr.operation.params[2]
                name = f"{name}_{theta}_{phi}_{lam}"
            elif name == "u2":
                theta = math.pi / 2
                phi = instr.operation.params[0]
                lam = instr.operation.params[1]
                name = f"{name}_{phi}_{lam}"
            elif name == "u1":
                theta = 0.0
                phi = 0.0
                lam = instr.operation.params[0]
                name = f"{name}_{lam}"
        mapping[i] = (name, input_idxs)

    return mapping


def line_gate_mapping(
    circuit: QuantumCircuit,
) -> dict[int, list[tuple[int, str]]]:
    """
    Returns a mapping of qubits to the ids and names of the gates that are executed on that qubit in order.
    SWAP gates are named 'swapi' where 'i' is the qubit on the other side of the SWAP.
    CX gates are named 'cx0-i' or 'cx1-i' depending on if they are the control or target qubit,
    where 'i' is the qubit on the other side of the CX.

    Example
    -------
    Given circuit:
         ┌───┐
    q_0: ┤ X ├──■──
         ├───┤┌─┴─┐
    q_1: ┤ X ├┤ X ├
         └───┘├───┤
    q_2: ──■──┤ X ├
         ┌─┴─┐└───┘
    q_3: ┤ X ├─────
         └───┘

    The mapping would be:
    `{0: [(0, 'x'),(3, 'cx0-1')], 1: [(1, 'x'),(3, 'cx1-0')], 2: [(2, 'cx0-3'),(4, 'x')], 3: [(2, 'cx1-2')]}`
    """
    gate_line_mapping = gate_line_dependency_mapping(circuit)
    mapping = {}

    for gate, (name, lines) in gate_line_mapping.items():
        for i, line in enumerate(lines):
            if not line in mapping.keys():
                mapping[line] = []
            if name == "swap":
                gate_name = f"{name}{lines[i-1]}"
            elif name == "cx":
                gate_name = f"{name}{i}-{lines[i-1]}"
            else:
                gate_name = name
            mapping[line].append((gate, gate_name))

    return mapping


def reinsert_unary_gates(
    original_circuit: QuantumCircuit,
    cx_circuit: QuantumCircuit,
    initial_mapping: dict[LogicalQubit, PhysicalQubit],
    ancillaries: bool,
):
    """
    Reinserts the unary gates from the original circuit into the CX circuit.
    """

    def get_gates_on_line(
        gates: list[tuple[int, str]], mapping: dict[int, tuple[str, list[int]]]
    ):
        def short_name(name: str):
            if name.startswith("cx"):
                return "cx"
            if name.startswith("swap"):
                return "swap"
            return name

        return [(short_name(g[1]), mapping[g[0]][1]) for g in gates]

    def consume_line_until_binary_gate(gate_list: list[tuple[str, list[int]]]):
        unary_gates = list(takewhile(lambda g: g[0] not in ["cx", "swap"], gate_list))
        rest = gate_list[len(unary_gates) :]
        return unary_gates, rest

    original_gate_line_dependency_mapping = gate_line_dependency_mapping(
        original_circuit
    )
    original_gate_list = {
        line: get_gates_on_line(gates, original_gate_line_dependency_mapping)
        for line, gates in line_gate_mapping(original_circuit).items()
    }
    cx_gate_line_dependency_mapping = gate_line_dependency_mapping(cx_circuit)
    cx_gate_list = {
        line: get_gates_on_line(gates, cx_gate_line_dependency_mapping)
        for line, gates in line_gate_mapping(cx_circuit).items()
    }

    register = QuantumRegister(cx_circuit.num_qubits, "q")
    result_circuit = QuantumCircuit(register)
    mapping = {k.id: v.id for k, v in initial_mapping.items()}
    all_pqubits_in_mapping = len(set(mapping.values())) == len(mapping.values())
    all_lqubits_in_mapping = len(set(mapping.keys())) == len(mapping.keys())
    if not all_pqubits_in_mapping or not all_lqubits_in_mapping:
        raise ValueError(
            f"Initial mapping '{mapping}' does not contain all logical and physical qubits. Perhaps the encoding is wrong?"
        )
    while not all(len(gates) == 0 for gates in original_gate_list.values()):
        # insert unary gates
        for line in range(original_circuit.num_qubits):
            if line not in original_gate_list.keys():
                continue
            unary_gates, rest = consume_line_until_binary_gate(original_gate_list[line])
            original_gate_list[line] = rest
            physical_line = mapping[line]
            for unary_gate in unary_gates:
                gate_name, _ = unary_gate
                match gate_name:
                    case "x":
                        result_circuit.x(physical_line)
                    case "h":
                        result_circuit.h(physical_line)
                    case "t":
                        result_circuit.t(physical_line)
                    case "tdg":
                        result_circuit.tdg(physical_line)
                    case "s":
                        result_circuit.s(physical_line)
                    case "sdg":
                        result_circuit.sdg(physical_line)
                    case "y":
                        result_circuit.y(physical_line)
                    case "z":
                        result_circuit.z(physical_line)
                    case name if name.startswith("rx"):
                        theta = float(name.split("_")[1])
                        result_circuit.rx(theta, physical_line)
                    case name if name.startswith("rz"):
                        phi = float(name.split("_")[1])
                        result_circuit.rz(phi, physical_line)
                    case name if name.startswith("u_"):
                        theta = float(name.split("_")[1])
                        phi = float(name.split("_")[2])
                        lam = float(name.split("_")[3])
                        result_circuit.u(theta, phi, lam, physical_line)
                    case name if name.startswith("u3"):
                        theta = float(name.split("_")[1])
                        phi = float(name.split("_")[2])
                        lam = float(name.split("_")[3])
                        instr = CircuitInstruction(
                            operation=Instruction(
                                name="u3",
                                num_qubits=1,
                                num_clbits=0,
                                params=[theta, phi, lam],
                            ),
                            qubits=(Qubit(register, physical_line),),
                            clbits=(),
                        )
                        result_circuit.append(instr)
                    case name if name.startswith("u2"):
                        phi = float(name.split("_")[1])
                        lam = float(name.split("_")[2])
                        instr = CircuitInstruction(
                            operation=Instruction(
                                name="u2",
                                num_qubits=1,
                                num_clbits=0,
                                params=[phi, lam],
                            ),
                            qubits=(Qubit(register, physical_line),),
                            clbits=(),
                        )
                        result_circuit.append(instr)
                    case name if name.startswith("u1"):
                        lam = float(name.split("_")[1])
                        instr = CircuitInstruction(
                            operation=Instruction(
                                name="u1",
                                num_qubits=1,
                                num_clbits=0,
                                params=[lam],
                            ),
                            qubits=(Qubit(register, physical_line),),
                            clbits=(),
                        )
                        result_circuit.append(instr)
                    case _:
                        raise ValueError(
                            f"Unknown unary gate: '{gate_name}'... Perhaps you should add it to the match statement?"
                        )

        def gate_with_unpacked_qubits(gate):
            name, lines = gate
            return name, lines[0], lines[1]

        def instructions_with_two_occurrences(instrs: list[tuple[str, int, int]]):
            return {
                instr
                for instr in instrs
                if sum(1 for instr2 in instrs if instr2 == instr) == 2
            }

        # find binary gates to add
        next_instructions = [
            gate_with_unpacked_qubits(gates[0])
            for gates in cx_gate_list.values()
            if gates
        ]
        binary_gates_to_add = instructions_with_two_occurrences(next_instructions)

        # pop relevant elements from cx_gate_list
        for line in cx_gate_list:
            empty = len(cx_gate_list[line]) == 0
            if empty:
                continue
            is_to_be_added = (
                gate_with_unpacked_qubits(cx_gate_list[line][0]) in binary_gates_to_add
            )
            if is_to_be_added:
                cx_gate_list[line].pop(0)

        # pop relevant elements from original_gate_list
        for line in original_gate_list:
            empty = len(original_gate_list[line]) == 0
            if empty:
                continue
            name, first, second = gate_with_unpacked_qubits(original_gate_list[line][0])
            is_to_be_added = (
                name,
                mapping[first],
                mapping[second],
            ) in binary_gates_to_add
            if is_to_be_added:
                original_gate_list[line].pop(0)

        # insert binary gates
        for gate in binary_gates_to_add:
            gate_name, first, second = gate
            if gate_name == "cx":
                result_circuit.cx(first, second)
            elif gate_name == "swap":
                result_circuit.swap(first, second)

                # fix mapping
                reverse_mapping = {v: k for k, v in mapping.items()}
                if ancillaries and (
                    first not in reverse_mapping.keys()
                    or second not in reverse_mapping.keys()
                ):
                    if (
                        first not in reverse_mapping.keys()
                        and second not in reverse_mapping.keys()
                    ):
                        pass
                    elif first not in reverse_mapping.keys():
                        second_logical = reverse_mapping[second]
                        mapping[second_logical] = first
                    else:
                        first_logical = reverse_mapping[first]
                        mapping[first_logical] = second
                else:
                    first_logical = reverse_mapping[first]
                    second_logical = reverse_mapping[second]
                    tmp = mapping[first_logical]
                    mapping[first_logical] = mapping[second_logical]
                    mapping[second_logical] = tmp

    return result_circuit


def remove_all_non_swap_gates(circuit: QuantumCircuit) -> QuantumCircuit:
    """
    Remove all non-SWAP gates from the circuit.
    """
    num_qubits = circuit.num_qubits
    qubit_name = circuit.qregs[0].name
    new_circuit = QuantumCircuit(QuantumRegister(num_qubits, qubit_name))
    for instr in circuit.data:
        if instr[0].name.startswith("swap"):
            new_circuit.append(instr[0], instr[1])

    return new_circuit


def make_final_mapping(
    circuit: QuantumCircuit,
    initial_mapping: dict[LogicalQubit, PhysicalQubit],
    ancillaries: bool,
) -> dict[LogicalQubit, PhysicalQubit]:
    reverse_mapping: dict[PhysicalQubit, LogicalQubit] = {
        p: q for q, p in initial_mapping.items()
    }
    only_swaps_circuit = remove_all_non_swap_gates(circuit)
    for instr in only_swaps_circuit.data:
        physical1 = PhysicalQubit(instr[1][0]._index)
        physical2 = PhysicalQubit(instr[1][1]._index)
        if ancillaries and (
            physical1 not in reverse_mapping.keys()
            or physical2 not in reverse_mapping.keys()
        ):
            if (
                physical1 not in reverse_mapping.keys()
                and physical2 not in reverse_mapping.keys()
            ):
                pass
            elif physical1 not in reverse_mapping.keys():
                reverse_mapping[physical1] = reverse_mapping[physical2]
                del reverse_mapping[physical2]
            else:
                reverse_mapping[physical2] = reverse_mapping[physical1]
                del reverse_mapping[physical1]
        else:
            tmp = reverse_mapping[physical1]
            reverse_mapping[physical1] = reverse_mapping[physical2]
            reverse_mapping[physical2] = tmp

    final_mapping = {q: p for p, q in reverse_mapping.items()}
    return final_mapping


def fill_holes_with_id_gates(circuit: QuantumCircuit) -> QuantumCircuit:
    """
    Fill holes in the circuit with identity gates.
    """
    idle_qubits_dict = {q: True for q in range(circuit.num_qubits)}
    for instr in circuit.data:
        for q in instr[1]:
            idle_qubits_dict[q._index] = False
    idle_qubits = {q for q in idle_qubits_dict if idle_qubits_dict[q]}

    num_qubits = circuit.num_qubits
    qubit_name = circuit.qregs[0].name
    new_circuit = QuantumCircuit(QuantumRegister(num_qubits, qubit_name))
    circuit_dag = circuit_to_dag(circuit)
    layers = circuit_dag.layers()
    all_qubits = set(range(num_qubits)) - idle_qubits

    for layer in layers:
        graph = layer["graph"]
        nodes = graph.nodes()
        instrs = [
            (node.op, node.qargs, node.cargs)
            for node in nodes
            if isinstance(node, DAGOpNode)
        ]
        occupied_qubits = {q._index for instr in instrs for q in instr[1]}
        unoccupied_qubits = all_qubits - occupied_qubits
        for q in unoccupied_qubits:
            new_circuit.id(q)
        for instr in instrs:
            new_circuit.append(instr[0], instr[1], instr[2])

    return new_circuit
