# Global filter: system/controller users bypass all OPA deny rules.
# The admission controller queries effective_deny instead of deny.
package kubernetes.admission

import data.helpers

# System controllers get no violations (bypass all restrictions)
effective_deny := set() if {
    helpers.is_system_or_controller_user
}

# All other users get the full deny set
effective_deny := deny if {
    not helpers.is_system_or_controller_user
}
