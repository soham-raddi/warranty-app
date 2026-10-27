"""The database layer, one module per topic. Import everything from here: `from digital_twin.db import get_appliance`.

Tables are created (or upgraded) once, when this package is first imported.
"""
from .connection import (  # noqa: F401
    close_connection,
    DATABASE_URL,
    DB_PATH,
    IS_POSTGRES,
    conn,
    cursor,
    end_request,
    insert_returning_id,
)
from .settings import (  # noqa: F401
    APPEARANCE_OPTIONS,
    CURRENCY_SYMBOLS,
    DEFAULT_SETTINGS,
    get_all_settings,
    get_appearance,
    get_setting_value,
    set_appearance,
    set_setting_value,
    workspace_today,
)
from .currency import (  # noqa: F401
    convert_amount,
    exchange_rates_date,
    foreign_currencies_in_use,
    get_exchange_rates,
    get_historical_rates,
    purchase_dates_missing_rates,
    save_exchange_rates,
    save_historical_rates,
)
from .users import (  # noqa: F401
    get_user_by_email,
    get_user_by_id,
    upsert_user,
)
from .workspaces import (  # noqa: F401
    INVITE_LIFETIME_DAYS,
    WORKSPACE_ROLES,
    accept_invite_token,
    accept_pending_invites,
    add_workspace_member,
    create_invite,
    create_workspace,
    delete_invite,
    delete_user_account,
    delete_workspace,
    get_invite_by_token,
    get_membership_role,
    get_user_workspaces,
    get_workspace,
    list_invites,
    list_workspace_members,
    owned_workspaces_with_other_members,
    prune_old_invites,
    regenerate_invite_token,
    remove_workspace_member,
    rename_workspace,
    transfer_ownership,
    workspace_member_emails,
)
from .items import (  # noqa: F401
    add_service_history_entry,
    apply_warranty_card_details,
    delete_appliance,
    delete_service_history_entry,
    export_workspace_rows,
    find_duplicate_receipt,
    get_all_appliances,
    get_appliance,
    get_appliance_file_paths,
    get_inventory_analytics,
    get_service_history,
    item_exists,
    parse_amount,
    relocate_upload_paths,
    save_receipt_to_db,
    update_appliance,
    update_service_history_entry,
    update_warranty_card,
)
from .chat import (  # noqa: F401
    clear_chat_messages,
    delete_chat_conversation,
    get_chat_conversations,
    get_chat_messages,
    save_chat_message,
    search_chat_messages,
)
from .mail import (  # noqa: F401
    add_pending_receipt,
    delete_pending_receipt,
    get_pending_receipt,
    list_pending_receipts,
    opted_out_emails,
    pending_receipt_exists,
    prune_old_pending_receipts,
    record_reminders_sent,
    reminder_already_sent,
    set_reminder_optout,
    workspace_by_inbound_token,
    workspaces_with_reminders_enabled,
)
from .ratelimits import (  # noqa: F401
    hit_rate_limit,
    prune_rate_limits,
)
from .schema import (  # noqa: F401
    PLACEHOLDER_VALUES,
    SCHEMA_VERSION,
    _migrate_existing_install,
    init_schema,
    pin_item_currencies,
)

init_schema()
# A one-time upgrade for installs from before workspaces existed (does nothing afterwards).
_migrate_existing_install()
