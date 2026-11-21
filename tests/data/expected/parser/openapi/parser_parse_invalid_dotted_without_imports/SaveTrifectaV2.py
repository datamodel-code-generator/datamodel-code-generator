class Shipment(BaseModel):
    trackingNumber: Optional[str] = None


class Field1(BaseModel):
    shipment: Optional[Shipment] = None
