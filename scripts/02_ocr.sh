#!/usr/bin/env bash
# 이미지 PDF 18종 OCR (kor+eng, 300dpi). 출력: data/ocr/<docid>/p###.txt
set -u
BASE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$(cd "$BASE/../2.연금/docs_renamed" && pwd)"
export TESSDATA_PREFIX="$BASE/.tessdata"
OUT="$BASE/data/ocr"; TMP="$HOME/ocr_tmp"
mkdir -p "$OUT" "$TMP"
DOCS="$(python3 -c "import json;print(' '.join(json.load(open('$BASE/data/_extract_summary.json'))['image_pdfs']))")"
for d in $DOCS; do
  [ -f "$OUT/$d/.done" ] && { echo "skip $d"; continue; }
  mkdir -p "$OUT/$d"; rm -rf "$TMP/$d"; mkdir -p "$TMP/$d"
  pdftoppm -r 300 -gray -png "$SRC/$d.pdf" "$TMP/$d/pg" 2>/dev/null
  ls "$TMP/$d"/pg-*.png 2>/dev/null | xargs -P 4 -I{} sh -c \
    'f="{}"; b=$(basename "$f" .png); tesseract "$f" "'"$OUT/$d"'/$b" -l kor+eng --psm 3 >/dev/null 2>&1'
  n=$(ls "$OUT/$d"/*.txt 2>/dev/null | wc -l); c=$(cat "$OUT/$d"/*.txt 2>/dev/null | wc -c)
  echo "$d pages=$n chars=$c"
  touch "$OUT/$d/.done"; rm -rf "$TMP/$d"
done
echo "OCR ALL DONE"
