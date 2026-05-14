from abc import ABC, abstractmethod

class MotionHAL(ABC):
    """
    Standard Interface for Next-Gen Motion Drivers.
    All methods must be non-blocking.
    """
    
    @abstractmethod
    def enable_all(self):
        """Request hardware unlock (Servo ON)."""
        pass
        
    @abstractmethod
    def disable_all(self):
        """Request hardware lock (Servo OFF / Brake ON)."""
        pass
        
    @abstractmethod
    def move_to(self, axis: int, position: float):
        """Command single axis to target."""
        pass
        
    @abstractmethod
    def stop_all(self):
        """Immediate motion halt."""
        pass
        
    @abstractmethod
    def is_enabled_all(self) -> bool:
        """True if all controlled axes are enabled."""
        pass
        
    @abstractmethod
    def is_enabled_any(self) -> bool:
        """True if at least one axis is enabled."""
        pass
        
    @abstractmethod
    def is_moving_all(self) -> bool:
        """True if any axis is still performing a profile."""
        pass
        
    @abstractmethod
    def get_positions(self) -> list[float]:
        """Read current encoder values."""
        pass
