#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHIPMENT_LOG="$PROJECT_DIR/logs/shipment-cron.log"
PRODUCT_LOG="$PROJECT_DIR/logs/product-cron.log"

SHIPMENT_CMD="cd $PROJECT_DIR && /usr/bin/flock -n /tmp/lingxing-shipment.lock .venv/bin/python main.py --job shipment --shipment-source all --write-db --debug-api >> $SHIPMENT_LOG 2>&1"
PRODUCT_CMD="cd $PROJECT_DIR && /usr/bin/flock -n /tmp/lingxing-product.lock .venv/bin/python main.py --job product --write-db --debug-api >> $PRODUCT_LOG 2>&1"

SHIPMENT_CRON_LINE="*/5 7-22 * * * $SHIPMENT_CMD"
PRODUCT_CRON_LINE="*/5 7-22 * * * $PRODUCT_CMD"

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/output"

tmp_file="$(mktemp)"
crontab -l 2>/dev/null |
  grep -v "lingxing-shipment-sync" |
  grep -v "lingxing-product-sync" |
  grep -v "$PROJECT_DIR.*main.py" > "$tmp_file" || true

{
  cat "$tmp_file"
  echo "# lingxing-shipment-sync"
  echo "$SHIPMENT_CRON_LINE"
  echo "# lingxing-product-sync"
  echo "$PRODUCT_CRON_LINE"
} | crontab -
rm -f "$tmp_file"

echo "Cron installed:"
echo "$SHIPMENT_CRON_LINE"
echo "$PRODUCT_CRON_LINE"
