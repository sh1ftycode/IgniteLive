"""The public key that signs Ignite's Flatpak repository (packaging/flatpak/ignite.gpg, the same bytes).

Flatpak only installs updates from a repository whose signatures it can check. This key goes into every
.flatpak bundle and .flatpakref install file, and Ignite adds it to installs made before the repository was
signed (core/updates.py, before "flatpak update"). Only the public half is here; the private half is a
GitHub secret (IGNITE_FLATPAK_GPG_PRIVATE). No imports: the Ignite server reads it too.
"""

FINGERPRINT = "4DA61CD631422EF4ACF3D59942DD6A9CC22F86A9"
PUBLIC_KEY_B64 = (
    "mDMEawXeDxYJKwYBBAHaRw8BAQdAfP6zQxqphlwzxrvwAQ/NmsnChQKSItAoj+PeF03MpsK0Gklnbml0ZSBGbGF0cGFrIHNpZ25p"
    "bmcga2V5iJMEExYKADsWIQRNphzWMUIu9Kzz1ZlC3Wqcwi+GqQUCawXeDwIbAwULCQgHAgIiAgYVCgkICwIEFgIDAQIeBwIXgAAK"
    "CRBC3Wqcwi+GqePcAQDnzf5UBkllUR54KvahVmFMy60ilgx9DQQZnEGIat7xfQD/YOYBEzARhyLTJ/rBxBBzpuOakPFsq+FBMuQT"
    "4ZBLbgw="
)
