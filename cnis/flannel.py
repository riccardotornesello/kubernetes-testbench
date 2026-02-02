from cnis.base import BaseCNI


class FlannelCNI(BaseCNI):
    def install_cni(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")


module = FlannelCNI
