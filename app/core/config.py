from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    DATABASE_URL: str = "sqlite:///./veille_dang.db"
    FCM_SERVER_KEY: str = ""
    FENETRE_ANALYSE: int = 90
    SEUIL_DECLIN_PCT: float = 30.0
    SEUIL_PIC_MULTIPLICATEUR: float = 2.0
    SEUIL_INACTIF_JOURS: int = 7
    SEUIL_AMBULANT_DISPARU: int = 14

settings = Settings()
