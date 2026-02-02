from cnis.base import BaseCNI


class KindNetCNI(BaseCNI):
    def install_cni(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")


module = KindNetCNI
