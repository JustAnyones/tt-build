import argparse
import enum
import os
import re
import shutil
import sys
import json
import logging
import tempfile
import zipfile

from dataclasses import dataclass, field
from io import BufferedReader
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("tt-build")

class OutputFormat(enum.Enum):
    STORE = "store"
    #ZIP = "zip" # not currently supported, might be added in the future

SUPPORTED_OUTPUT_FORMATS = [format.value for format in OutputFormat]
TEMP_BUILD_DIR = "temp_build"

IGNORED_EXTENSIONS = [".py", ".md", ".sh"]

@dataclass
class Config:
    input_directory: str
    output_directory: str
    output_format: OutputFormat
    # These are files and directories that start with . or _
    # See https://pca.svetikas.lt/docs/reference/technical/loading-order/#ignored_files_and_directories
    exclude_ignored_items: bool = True
    ignored_extensions: list[str] = field(default_factory=lambda: IGNORED_EXTENSIONS)
    ignored_directories: list[str] = field(default_factory=lambda: ["Redundancy"])
    strict_lua: bool = False
    mute_lua: bool = True

def optimize_json(config: Config, f: BufferedReader) -> str:
    """Returns a string of cleaned JSON of a file."""
    new_lines: list[str] = []
    for raw_bytes in f.readlines():
        try:
            line = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            logger.exception("Failed decoding", raw_bytes)
            raise e

        in_str = False
        if "//" in line:
            for i, char in enumerate(line):
                if in_str:
                    if char == "\\":
                        continue
                    elif char == "\"":
                        in_str = False
                elif char == "\"":
                    in_str = True
                elif char == "/" and i + 1 < len(line) and line[i + 1] == "/":
                    line = line[0:i]
                    break

        line = re.sub(r'(\"[^\"]*\"\s*:\s*)([^\"\[{\s][^,}\]\"]*[a-zA-Z][^,}\]\"\s]*)(\s*[,}])', r'\1"\2"\3', line)

        # Remove invalid characters
        line = line.replace("﻿", "")
        line = line.replace(";", ",")
        line = line.replace("\t", " ")
        line = line.replace("\r", "")
        line = line.replace("\n", " ")
        line = line.replace(" ", " ")
        line = line.replace(" ", "")

        new_lines.append(line)

    # Handle escaping of slashes
    stack = 0
    in_str = False
    full_string = ''.join(new_lines)
    for i in range(len(full_string)):
        if in_str:
            if full_string[i] == "\"":
                in_str = False
        elif full_string[i] == "\"":
            in_str = True
        elif full_string[i] == "[":
            stack += 1
        elif full_string[i] == "]":
            stack -= 1
            if stack == 0:
                full_string = full_string[0:i + 1]
                break

    # Load data into a list for easier handling
    data: list[dict[str, Any]] = json.loads(full_string)

    for arr_obj in data:
        obj_keys = list(arr_obj.copy().keys())
        plugin_id: str = arr_obj.get("id", "unknown id")
        logger.debug(f"analysing ID: {plugin_id}")

        # Raise exception if privileged tag is detected,
        # as it is deprecated and no longer considered secure
        if "privileged" in obj_keys:
            raise Exception(
                f"Privileged tag detected in plugin {plugin_id}. This tag is deprecated and no longer considered secure." +
                "Please replace it with \"require privileges\": true."
            )

        # Remove strict lua
        if  "strict lua" in obj_keys:
            del arr_obj["strict lua"]
            logger.info(f"{plugin_id}: removed strict lua attribute")

        # If any scripts objects are deteced, force add mute lua
        if "script" in obj_keys or "scripts" in obj_keys:
            arr_obj["mute lua"] = True
            logger.info(f"{plugin_id}: muted Lua")

    return json.dumps(data, ensure_ascii=False)

def create_archive(
    config: Config,
    manifest: dict[str, Any]
) -> str:
    # Create archive name based on manifest title and version
    archive_name = f"{manifest['title']} {manifest['version']}.zip"

    # Prepare a temporary directory for storing processed JSON
    tmp_build_directory = tempfile.mkdtemp(prefix="tt-build-")

    # List of files to include in the archive, as tuples of (source path, path in the archive).
    files_to_include: list[tuple[str, str]] = []
    files_to_remove_from_archive: list[str] = []

    # Go over each file, optimize it and put it in a zip
    for dirname, _, files in os.walk(config.input_directory):
        for filename in files:
            # Get absolute path of the file
            src_abs_file_path = os.path.join(dirname, filename)
            # Get relative path of the file to the input directory, so we can preserve the folder structure in the archive
            src_rel_file_path = os.path.relpath(os.path.join(dirname, filename), config.input_directory)
            # Path where the processed file will be stored temporarily before being added to the archive
            temp_file_path = os.path.join(tmp_build_directory, src_rel_file_path)

            # Ignore files with certain extensions
            if any(filename.endswith(ext) for ext in config.ignored_extensions):
                logger.info(f"Ignoring file with extension: {src_abs_file_path}")
                continue

            # Ignore files and directories starting with . or _ if the config option is enabled
            if config.exclude_ignored_items and (filename.startswith(".") or filename.startswith("_") or any(part.startswith(".") or part.startswith("_") for part in src_rel_file_path.split(os.sep))):
                logger.info(f"Ignoring file or directory starting with . or _: {src_abs_file_path}")
                continue

            # Ignore files in ignored directories
            if any(ignored_dir in src_rel_file_path.split(os.sep) for ignored_dir in config.ignored_directories):
                logger.info(f"Ignoring file in ignored directory: {src_abs_file_path}")
                continue

            # Create all missing directories of the relative path in temp build directory
            temp_file_dir = os.path.dirname(temp_file_path)
            if not os.path.exists(temp_file_dir):
                os.makedirs(temp_file_dir)
                logger.debug(f"Created missing temp directory: {temp_file_dir}")

            # If it's a plugin.manifest file and we're in plugin store mode, we can remove thumbnail
            # as the manifest is actually not used in the plugin store and the thumbnail is just a waste of space
            if src_rel_file_path == "plugin.manifest" and config.output_format == OutputFormat.STORE:
                logger.info(f"Optimizing plugin.manifest file: {src_abs_file_path}")
                if "thumbnail" in manifest:
                    # Mark thumbnail for removal from archive
                    thumbnail_path = os.path.join(config.input_directory, manifest["thumbnail"])
                    if os.path.exists(thumbnail_path):
                        files_to_remove_from_archive.append(thumbnail_path)
                    del manifest["thumbnail"]
                    logger.info("Removed thumbnail from plugin.manifest")
                with open(temp_file_path, "w") as f:
                    json.dump(manifest, f, ensure_ascii=False)
                files_to_include.append((temp_file_path, src_rel_file_path))
                continue

            # If it's a JSON file, do some manual optimizations
            if filename.endswith('.json'):
                logger.info(f"Optimizing JSON file: {src_abs_file_path}")
                in_file = open(src_abs_file_path, mode="rb")
                out_file = open(temp_file_path, mode="w")
                _ = out_file.write(optimize_json(config, in_file))
                out_file.close()
                in_file.close()
                files_to_include.append((temp_file_path, src_rel_file_path))
                continue
            files_to_include.append((src_abs_file_path, src_rel_file_path))


    # Open the zip file for writing and add all the files to it, preserving the folder structure
    zf = zipfile.ZipFile(os.path.join(config.output_directory, archive_name), "w")
    for src_path, dst_path in files_to_include:
        if src_path in files_to_remove_from_archive:
            logger.info(f"Skipping file marked for removal: {src_path}")
            continue
        zf.write(filename=src_path, arcname=dst_path)
        logger.info(f"Added file to archive: {dst_path}")
    zf.close()

    # Clean up temporary build directory
    shutil.rmtree(tmp_build_directory)

    return os.path.join(config.output_directory, archive_name)




def main():
    parser = argparse.ArgumentParser(description="A tool to produce optimized plugin archives for TheoTown.")
    _ = parser.add_argument(
        "--input-directory", "-i", type=str,
        default=".",
        help="The directory of the plugin to be processed. Must contain a plugin.manifest file. Default is the current directory.",
    )
    _ = parser.add_argument(
        "--output-directory", "-o", type=str,
        default="output",
        help="The directory where the optimized plugin archive will be saved. Default is 'output'.",
    )
    _ = parser.add_argument(
        "--output-format", "-f", type=str,
        default=OutputFormat.STORE.value,
        help="The format of the output archive. Supported formats are: " + ", ".join(SUPPORTED_OUTPUT_FORMATS) + ". Default is 'store'.",
    )

    # Convert args to a config for type hinting
    args = parser.parse_args()
    try:
        output_format = OutputFormat(args.output_format)
    except ValueError:
        logger.error(f"Unsupported output format: {args.output_format}. Supported formats are: " + ", ".join(SUPPORTED_OUTPUT_FORMATS))
        sys.exit(1)
    
    # Get plugin directory and check if it exists
    plugin_dir: str = os.path.abspath(args.input_directory)
    if not os.path.exists(plugin_dir):
        logger.error(f"Directory {plugin_dir} does not exist.")
        sys.exit(1)

    # Read manifest and check if it exists
    manifest_path = os.path.join(plugin_dir, "plugin.manifest")
    if not os.path.exists(manifest_path):
        logger.error(f"Manifest file {manifest_path} does not exist.")
        sys.exit(1)
    
    # Read manifest and check if it's a valid JSON object
    with open(manifest_path, "r") as f:
        manifest = json.load(f)
        if not isinstance(manifest, dict):
            logger.error(f"Manifest file {manifest_path} should be a JSON object.")
            sys.exit(1)

    # Create output directory if it doesn't exist
    output_dir: str = os.path.abspath(args.output_directory)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    config = Config(
        input_directory=plugin_dir,
        output_directory=output_dir,
        output_format=output_format,
    )
    logger.info(f"Processing plugin: {manifest['title']} (version {manifest['version']})")
    archive_path = create_archive(config, manifest)
    logger.info(f"Archive created at: {archive_path}")

if __name__ == "__main__":
    main()