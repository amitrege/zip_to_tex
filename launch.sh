#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

usage() {
  cat <<EOF
Usage: ./launch.sh /absolute/or/relative/path/to/paper.zip|paper.tar.gz [zip-to-tex options]

Moves the archive into this repo directory, runs the flatten/compile pipeline here,
and leaves the final <archive_stem>_flat/ output folder in this directory.
By default this launcher skips PDF compilation and produces the flattened .tex.
Pass --compile if you also want to try building the PDF.

Examples:
  ./launch.sh ~/Downloads/1234.56789v1.zip
  ./launch.sh ~/Downloads/1234.56789v1.tar.gz
  ./launch.sh "~/Downloads/paper source.tar.gz" --engine xelatex --max-runs 6
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

if [[ "${1}" == "-h" || "${1}" == "--help" ]]; then
  usage
  exit 0
fi

SOURCE_INPUT="$1"
shift

if [[ ! -f "${SOURCE_INPUT}" ]]; then
  echo "Archive file not found: ${SOURCE_INPUT}" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${SOURCE_INPUT}")" && pwd -P)"
SOURCE_NAME="$(basename "${SOURCE_INPUT}")"
SOURCE_PATH="${SOURCE_DIR}/${SOURCE_NAME}"

case "${SOURCE_NAME}" in
  *.tar.gz)
    ARCHIVE_STEM="${SOURCE_NAME%.tar.gz}"
    ;;
  *.tgz)
    ARCHIVE_STEM="${SOURCE_NAME%.tgz}"
    ;;
  *.zip)
    ARCHIVE_STEM="${SOURCE_NAME%.zip}"
    ;;
  *)
    echo "Expected a .zip, .tar.gz, or .tgz file: ${SOURCE_PATH}" >&2
    exit 1
    ;;
esac

DEST_PATH="${REPO_DIR}/${SOURCE_NAME}"
OUTPUT_DIR="${REPO_DIR}/${ARCHIVE_STEM}_flat"

if [[ -e "${OUTPUT_DIR}" ]]; then
  echo "Output directory already exists: ${OUTPUT_DIR}" >&2
  exit 1
fi

if [[ "${SOURCE_PATH}" != "${DEST_PATH}" && -e "${DEST_PATH}" ]]; then
  echo "An archive with the same name already exists here: ${DEST_PATH}" >&2
  exit 1
fi

if [[ "${SOURCE_PATH}" != "${DEST_PATH}" ]]; then
  echo "Moving ${SOURCE_PATH} -> ${DEST_PATH}"
  mv "${SOURCE_PATH}" "${DEST_PATH}"
else
  echo "Using archive already in repo: ${DEST_PATH}"
fi

echo "Running pipeline in ${REPO_DIR}"
exec env PYTHONPATH="${REPO_DIR}/src" python3 -m zip_to_tex "${DEST_PATH}" --output-root "${REPO_DIR}" --no-compile "$@"
