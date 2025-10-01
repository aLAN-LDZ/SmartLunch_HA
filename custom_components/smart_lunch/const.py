DOMAIN = "smart_lunch"
PLATFORMS: list[str] = ["sensor", "select"]

DEFAULT_BASE = "https://app.smartlunch.pl"
LOGIN_PATH = "/users/sign_in"
USERS_ME_PATH = "/employees/api/v1/users"
FUNDING_PATH_TPL = "/employees/api/v1/funding_settings/{day}"
DELIVERY_PLACES_PATH = "/employees/api/v1/delivery_places"

USER_AGENT = "homeassistant-smartlunch/0.1"
COOKIE_KEYS = ["_smartlunch_session", "remember_user_token", "lang", "country"]
HTTP_TIMEOUT = 25
RETRY_STATUSES = {429, 500, 502, 503, 504}