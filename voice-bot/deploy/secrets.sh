#!/usr/bin/env bash
# Provider secrets for the voice server, kept as SSM SecureString /docai-voice/secrets/<NAME> (never in git).
# The server reads them at start-up as environment variables of the same name.
#
#   deploy/secrets.sh list                    # names and dates only, never values
#   deploy/secrets.sh set PLIVO_AUTH_TOKEN    # asks for the value (hidden); or: printf %s "$V" | ... set NAME
#   deploy/secrets.sh generate PHONE_URL_KEY  # random 40-character value
#   deploy/secrets.sh delete EXOTEL_API_TOKEN
#   deploy/secrets.sh exotel-urls             # the Voicebot applet URL and status callback URL to paste in Exotel
#   then: deploy/update.sh --restart          # the running server picks the change up
#
# Phone (Plivo): PLIVO_AUTH_ID PLIVO_AUTH_TOKEN PLIVO_NUMBER, PHONE_URL_KEY (generate), OUTBOUND_ALLOWED_TO
#   (team phones that may be rung, +91..., comma-separated), TELECALLER_NUMBER (transfer to a human)
# Phone (Exotel): EXOTEL_SID EXOTEL_API_KEY EXOTEL_API_TOKEN EXOTEL_EXOPHONE (+ PHONE_URL_KEY, OUTBOUND_ALLOWED_TO).
#   Exotel's URLs carry a key derived from PHONE_URL_KEY (never the key itself): see exotel-urls.
# WhatsApp Business Calling: WHATSAPP_TOKEN WHATSAPP_PHONE_NUMBER_ID WHATSAPP_APP_SECRET
#   WHATSAPP_WEBHOOK_VERIFICATION_TOKEN (and deploy/deploy.sh --whatsapp on for the WebRTC ports)
# Any other variable of backend/.env.example works the same way (one parameter per variable).
# ORIGIN_VERIFY is managed by deploy.sh (CloudFront header) and is refused here.
source "$(dirname "$0")/lib.sh"
need aws python3

cmd="${1:-}"; name="${2:-}"
check_name() {
  [[ "$name" =~ ^[A-Z][A-Z0-9_]{1,63}$ ]] || die "secret names are upper-case env names like PLIVO_AUTH_TOKEN"
  case "$name" in ORIGIN_VERIFY|ORIGIN_VERIFY_PREVIOUS) die "$name is managed by deploy.sh";; esac
}
case "$cmd" in
  list)
    aws ssm describe-parameters --parameter-filters "Key=Path,Option=Recursive,Values=$SSM_PREFIX/secrets" \
      --query 'Parameters[].[Name, LastModifiedDate]' --output text \
      | sed "s#^$SSM_PREFIX/secrets/##" | sort | awk '{ printf "  %-40s %s\n", $1, $2 }';;
  set)
    check_name
    (umask 077; : > "$WORK/value")
    if [ -t 0 ]; then
      read -r -s -p "Value for $name (hidden): " value; echo
      printf '%s' "$value" > "$WORK/value"; unset value
    else
      cat > "$WORK/value"
    fi
    [ -s "$WORK/value" ] || die "empty value"
    ssm_put_secure "$SSM_PREFIX/secrets/$name" "$WORK/value" "voice bot secret (deploy/secrets.sh)" overwrite \
      || die "could not save $name"
    rm -f "$WORK/value"
    say "saved $SSM_PREFIX/secrets/$name; apply with deploy/update.sh --restart";;
  generate)
    check_name
    (umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(30))' > "$WORK/value")
    ssm_put_secure "$SSM_PREFIX/secrets/$name" "$WORK/value" "voice bot secret (generated)" overwrite || die "could not save $name"
    rm -f "$WORK/value"
    say "generated $SSM_PREFIX/secrets/$name; apply with deploy/update.sh --restart";;
  delete)
    check_name
    aws ssm delete-parameter --name "$SSM_PREFIX/secrets/$name" && say "deleted $name; apply with deploy/update.sh --restart";;
  exotel-urls)
    # Exotel has no request signature: the key in these URLs is the only check, so keep them private.
    load_outputs
    [ -n "$OUT_DistributionDomainName" ] || die "stack $STACK has no CloudFront domain yet: run deploy/deploy.sh"
    url_key=$(aws ssm get-parameter --name "$SSM_PREFIX/secrets/PHONE_URL_KEY" --with-decryption \
                --query Parameter.Value --output text 2>/dev/null \
              | (cd "$VOICE_ROOT/backend" && python3 -c 'import sys; from config import exotel_url_key; print(exotel_url_key(sys.stdin.read().strip()))')) \
      || die "PHONE_URL_KEY is not set or not readable: deploy/secrets.sh generate PHONE_URL_KEY"
    [ -n "$url_key" ] || die "PHONE_URL_KEY is empty: deploy/secrets.sh generate PHONE_URL_KEY"
    echo "Voicebot applet URL (inbound flow): wss://$OUT_DistributionDomainName/phone/exotel/ws/$url_key?sample-rate=16000"
    echo "Status callback URL (optional)    : https://$OUT_DistributionDomainName/phone/exotel/status/$url_key"
    echo "Outbound calls build these URLs themselves. A new PHONE_URL_KEY changes both: paste them again.";;
  ""|-h|--help) usage "$0";;
  *) die "unknown command $cmd (list, set, generate, delete, exotel-urls)";;
esac
