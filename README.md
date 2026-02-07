# tt-build

A tool to produce optimized plugin archives for TheoTown. Primarily used by DSA.

## Features

- **JSON Optimization**: Removes comments, cleans formatting, and minimizes JSON files
- **Smart File Filtering**: Automatically excludes development files and directories, thumbnail files
- **JSON Attribute Handling**: Automatically changes Lua-related attributes
- **ZIP Archive Creation**: Packages plugins into ready-to-distribute archives

## Installation

Clone the repository:

```bash
git clone https://github.com/JustAnyones/tt-build.git
cd tt-build
```

Requirements:
- Python 3.12 or higher

Build and install:

```bash
python -m build --wheel --no-isolation
python -m installer dist/*.whl
```

## Usage

After installation:

```bash
tt-build -i ~/TheoTown/plugins/dsa -o ~/TheoTown/exported-plugins
```

Or run directly without installation:

```bash
python tt_build/cli.py -i ~/TheoTown/plugins/dsa -o ~/TheoTown/exported-plugins
```

### Command-Line Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--input-directory` | `-i` | `.` | Directory of the plugin to be processed (must contain plugin.manifest) |
| `--output-directory` | `-o` | `output` | Directory where the optimized archive will be saved |
| `--output-format` | `-f` | `store` | Output archive format (currently only `store` is supported) |

## Actions performed

### JSON Optimization

The tool performs several optimizations on JSON files:

1. **Comment Removal**: Strips `//` style comments while preserving strings
2. **Format Cleaning**: Removes invalid characters, normalizes whitespace
3. **Value Quoting**: Ensures unquoted values are properly quoted
4. **Attribute Management**:
   - Removes deprecated `privileged` tags (throws error if found)
   - Removes `strict lua` attributes
   - Automatically adds `mute lua: true` for plugins with scripts

### File Filtering

By default, the tool excludes:

- **File Extensions**: `.py`, `.md`, `.sh`
- **Hidden Files**: Files/directories starting with `.` or `_`
- **Ignored Directories**: `Redundancy` folder
- Thumbnail files that are specified in the plugin manifest, if running in store mode

### Output Formats

- **store**: Optimized for plugin store distribution
  - Removes thumbnail references from plugin.manifest
  - Excludes thumbnail files from archive

## Output

The tool creates a ZIP archive named:
```
{plugin_title} {plugin_version}.zip
```

For example: `My Awesome Plugin 1.zip`
