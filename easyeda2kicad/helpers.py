# Global imports
import json
import logging
import math
import os
import re
from datetime import datetime
from glob import escape

from easyeda2kicad import __version__
from easyeda2kicad.kicad.parameters_kicad_symbol import KicadVersion, sanitize_fields

sym_lib_regex_pattern = {
    "v5": r"(#\n# {component_name}\n#\n.*?ENDDEF\n)",
    "v6": r'\n  \(symbol "{component_name}".*?\n  \)',
    "v6_99": r"",
}


def set_logger(log_file: str, log_level: int) -> None:

    root_log = logging.getLogger()
    root_log.setLevel(log_level)

    if log_file:
        file_handler = logging.FileHandler(
            filename=log_file, mode="w", encoding="utf-8"
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(
            logging.Formatter(
                fmt="[{asctime}][{levelname}][{funcName}] {message}", style="{"
            )
        )
        root_log.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(
        logging.Formatter(fmt="[{levelname}] {message}", style="{")
    )
    root_log.addHandler(stream_handler)


def sanitize_for_regex(field: str):
    return re.escape(field)


def id_already_in_symbol_lib(
    lib_path: str, component_name: str, kicad_version: KicadVersion
) -> bool:
    with open(lib_path, encoding="utf-8") as lib_file:
        current_lib = lib_file.read()
        component = re.findall(
            sym_lib_regex_pattern[kicad_version.name].format(
                component_name=sanitize_for_regex(component_name)
            ),
            current_lib,
            flags=re.DOTALL,
        )
        if component != []:
            logging.warning(f"This id is already in {lib_path}")
            return True
    return False


# Regex locating a part's LCSC id field inside a symbol lib, keyed by kicad version.
# v5 stores it as `F6 "C123"`, v6 as a `"LCSC Part"` property whose value is the id.
# v6 must tolerate both layouts the value appears in: the script emits key and value
# on separate lines, but KiCad re-normalizes on save to a single line — so `\s+`
# (any run of spaces/newlines/tabs) sits between them either way.
lcsc_id_field_pattern = {
    "v5": r'F6 "{lcsc_id}"',
    "v6": r'"LCSC Part"\s+"{lcsc_id}"',
}


def find_symbol_by_lcsc_id(
    lib_path: str, lcsc_id: str, kicad_version: KicadVersion
):
    """Look up a symbol by its LCSC id and return (symbol_name, footprint_name).

    Returns None when no symbol carries this LCSC id. footprint_name is None when
    the symbol has no footprint field. The lookup is fully offline.
    """
    # 1. Bail out early if the lib file does not even exist yet.
    if not os.path.isfile(lib_path):
        return None

    with open(lib_path, encoding="utf-8") as lib_file:
        lib = lib_file.read()

    # 2. Find the LCSC id field; its absence means the part is not in the lib.
    match = re.search(
        lcsc_id_field_pattern[kicad_version.name].format(
            lcsc_id=sanitize_for_regex(lcsc_id)
        ),
        lib,
    )
    if not match:
        return None

    # 3. The symbol's own name/footprint fields are the nearest ones *preceding*
    #    the LCSC field, so scan only the text before it and take the last hit.
    head = lib[: match.start()]
    if kicad_version == KicadVersion.v6:
        # Top-level symbols sit at one indent unit (one tab or two spaces); their
        # nested sub-symbols ("name_0_1") sit deeper, so anchoring to exactly one
        # tab / two spaces after a newline skips the children. Footprint matching
        # tolerates single- and multi-line layouts (see lcsc_id_field_pattern).
        names = re.findall(r'\n(?:\t|  )\(symbol "([^"]+)"', head)
        footprints = re.findall(r'"Footprint"\s+"([^"]+)"', head)
    else:
        names = re.findall(r"\nDEF (\S+) ", head)
        footprints = re.findall(r'F2 "([^"]+)"', head)

    symbol_name = names[-1] if names else lcsc_id
    # Footprint fields are stored as "lib:name"; keep just the file-name part.
    footprint_name = footprints[-1].split(":")[-1] if footprints else None
    return symbol_name, footprint_name


def update_component_in_symbol_lib_file(
    lib_path: str,
    component_name: str,
    component_content: str,
    kicad_version: KicadVersion,
) -> None:
    with open(file=lib_path, encoding="utf-8") as lib_file:
        current_lib = lib_file.read()
        new_lib = re.sub(
            sym_lib_regex_pattern[kicad_version.name].format(
                component_name=sanitize_for_regex(component_name)
            ),
            component_content,
            current_lib,
            flags=re.DOTALL,
        )

        new_lib = new_lib.replace(
            "(generator kicad_symbol_editor)",
            "(generator https://github.com/uPesy/easyeda2kicad.py)",
        )

    with open(file=lib_path, mode="w", encoding="utf-8") as lib_file:
        lib_file.write(new_lib)


def add_component_in_symbol_lib_file(
    lib_path: str, component_content: str, kicad_version: KicadVersion
) -> None:

    if kicad_version == KicadVersion.v5:
        with open(file=lib_path, mode="a+", encoding="utf-8") as lib_file:
            lib_file.write(component_content)
    elif kicad_version == KicadVersion.v6:
        with open(file=lib_path, mode="rb+") as lib_file:
            lib_file.seek(-2, 2)
            lib_file.truncate()
            lib_file.write(component_content.encode(encoding="utf-8"))
            lib_file.write("\n)".encode(encoding="utf-8"))

        with open(file=lib_path, encoding="utf-8") as lib_file:
            new_lib_data = lib_file.read()

        with open(file=lib_path, mode="w", encoding="utf-8") as lib_file:
            lib_file.write(
                new_lib_data.replace(
                    "(generator kicad_symbol_editor)",
                    "(generator https://github.com/uPesy/easyeda2kicad.py)",
                )
            )


def get_local_config() -> dict:
    if not os.path.isfile("easyeda2kicad_config.json"):
        with open(file="easyeda2kicad_config.json", mode="w", encoding="utf-8") as conf:
            json.dump(
                {"updated_at": datetime.utcnow().timestamp(), "version": __version__},
                conf,
                indent=4,
                ensure_ascii=False,
            )
        logging.info("Create easyeda2kicad_config.json config file")

    with open(file="easyeda2kicad_config.json", encoding="utf-8") as conf:
        local_conf: dict = json.load(conf)

    return local_conf


def get_arc_center(start_x, start_y, end_x, end_y, rotation_direction, radius):
    arc_distance = math.sqrt(
        (end_x - start_x) * (end_x - start_x) + (end_y - start_y) * (end_y - start_y)
    )

    m_x = (start_x + end_x) / 2
    m_y = (start_y + end_y) / 2
    u = (end_x - start_x) / arc_distance
    v = (end_y - start_y) / arc_distance
    h = math.sqrt(radius * radius - (arc_distance * arc_distance) / 4)

    center_x = m_x - rotation_direction * h * v
    center_y = m_y + rotation_direction * h * u

    return center_x, center_y


def get_arc_angle_end(
    center_x: float, end_x: float, radius: float, flag_large_arc: bool
):
    theta = math.acos((end_x - center_x) / radius) * 180 / math.pi
    return 180 + theta if flag_large_arc else 180 + theta


def get_middle_arc_pos(
    center_x: float,
    center_y: float,
    radius: float,
    angle_start: float,
    angle_end: float,
):
    middle_x = center_x + radius * math.cos((angle_start + angle_end) / 2)
    middle_y = center_y + radius * math.sin((angle_start + angle_end) / 2)
    return middle_x, middle_y
