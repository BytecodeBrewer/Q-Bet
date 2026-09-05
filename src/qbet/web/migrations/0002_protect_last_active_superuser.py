from django.db import migrations


_CREATE_GUARD = r"""
CREATE OR REPLACE FUNCTION qbet_protect_last_active_superuser()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    other_active_admins integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_active AND OLD.is_staff AND OLD.is_superuser THEN
            SELECT COUNT(*)
              INTO other_active_admins
              FROM auth_user
             WHERE id <> OLD.id
               AND is_active
               AND is_staff
               AND is_superuser;
            IF other_active_admins = 0 THEN
                RAISE EXCEPTION 'Q-Bet must retain at least one active staff superuser with admin access.';
            END IF;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.is_active AND OLD.is_staff AND OLD.is_superuser
       AND NOT (NEW.is_active AND NEW.is_staff AND NEW.is_superuser) THEN
        SELECT COUNT(*)
          INTO other_active_admins
          FROM auth_user
         WHERE id <> OLD.id
           AND is_active
           AND is_staff
           AND is_superuser;
        IF other_active_admins = 0 THEN
            RAISE EXCEPTION 'Q-Bet must retain at least one active staff superuser with admin access.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS qbet_protect_last_active_superuser_trigger ON auth_user;
CREATE TRIGGER qbet_protect_last_active_superuser_trigger
BEFORE UPDATE OF is_active, is_staff, is_superuser OR DELETE
ON auth_user
FOR EACH ROW
EXECUTE FUNCTION qbet_protect_last_active_superuser();
"""

_DROP_GUARD = r"""
DROP TRIGGER IF EXISTS qbet_protect_last_active_superuser_trigger ON auth_user;
DROP FUNCTION IF EXISTS qbet_protect_last_active_superuser();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("web", "0001_simulation_control"),
    ]

    operations = [
        migrations.RunSQL(_CREATE_GUARD, _DROP_GUARD),
    ]
