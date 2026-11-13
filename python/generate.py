#!/usr/bin/env python3
"""Génère les stubs Python à partir du contrat .proto (dans ./generated).

À relancer chaque fois que devices.proto change.
"""
from importlib import resources
from pathlib import Path
import sys

from grpc_tools import protoc

root = Path(__file__).resolve().parent
proto_dir = root.parent / "proto"
out = root / "generated"
out.mkdir(exist_ok=True)

# Les .proto "well-known" (empty, timestamp...) sont embarqués dans grpc_tools ;
# la ligne de commande les ajoute seule, pas protoc.main() appelé depuis Python.
wkt_dir = resources.files("grpc_tools") / "_proto"

code = protoc.main([
    "grpc_tools.protoc",
    f"-I{proto_dir}",
    f"-I{wkt_dir}",
    f"--python_out={out}",
    f"--grpc_python_out={out}",
    f"--pyi_out={out}",
    str(proto_dir / "startracker/devices/v1/devices.proto"),
])
sys.exit(code)
