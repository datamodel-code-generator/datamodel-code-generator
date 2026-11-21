"""Review R21, N2: return __secret in a method reads the mangled _C__secret, not the module's __secret."""

__secret = 1


class C:
    def m(self):
        return __secret
