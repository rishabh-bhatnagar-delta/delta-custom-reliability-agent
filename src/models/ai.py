import dataclasses

from pydantic import BaseModel


@dataclasses.dataclass
class Tool:
    name: str
    description: str
    expected_output_class: BaseModel
