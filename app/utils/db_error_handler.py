"""
Database error handling utilities.

This module provides utilities for standardized database error handling,
including specific exception types, error logging, and user-friendly
error messages.
"""

import logging
import functools
import traceback
from typing import Dict, Any, Optional, Callable, TypeVar, cast

from flask import flash, jsonify
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

from app import db

# Type variable for generic function return type
T = TypeVar('T')

# Error message mapping
DB_ERROR_MESSAGES = {
    # IntegrityError usually means a constraint violation (unique, foreign key, etc.)
    IntegrityError: "The operation could not be completed due to a data conflict. This might be because the record already exists or references invalid data.",
    
    # OperationalError usually means a connection or timeout issue
    OperationalError: "The database is currently unavailable. Please try again later.",
    
    # Generic SQLAlchemy error
    SQLAlchemyError: "A database error occurred. Our team has been notified.",
    
    # Catch-all for any other exception
    Exception: "An unexpected error occurred. Please try again later."
}

def handle_db_errors(
    func: Optional[Callable] = None, 
    *,
    json_response: bool = False,
    flash_message: bool = True,
    log_error: bool = True,
    custom_error_messages: Optional[Dict[Any, str]] = None,
    status_code: int = 500
) -> Callable:
    """
    Decorator for handling database errors in a standardized way.
    
    Args:
        func: The function to decorate
        json_response: Whether to return a JSON response for errors
        flash_message: Whether to flash error messages
        log_error: Whether to log errors
        custom_error_messages: Custom error messages to use
        status_code: HTTP status code to use for errors
        
    Returns:
        The decorated function
    """
    error_messages = DB_ERROR_MESSAGES.copy()
    if custom_error_messages:
        error_messages.update(custom_error_messages)
        
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except HTTPException:
                # Let HTTP exceptions pass through unchanged
                raise
            except IntegrityError as e:
                db.session.rollback()
                msg = error_messages.get(IntegrityError, DB_ERROR_MESSAGES[IntegrityError])
                
                if log_error:
                    logging.error(f"IntegrityError in {func.__name__}: {str(e)}")
                    logging.debug(traceback.format_exc())
                
                if flash_message:
                    flash(msg, "error")
                
                if json_response:
                    return jsonify(success=False, message=msg), status_code
                
                # Re-raise as HTTPException with appropriate status code
                if not flash_message and not json_response:
                    raise HTTPException(response=msg, code=status_code)
                    
                return cast(T, None)  # Return None if we've handled the error
                
            except OperationalError as e:
                db.session.rollback()
                msg = error_messages.get(OperationalError, DB_ERROR_MESSAGES[OperationalError])
                
                if log_error:
                    logging.error(f"OperationalError in {func.__name__}: {str(e)}")
                    logging.debug(traceback.format_exc())
                
                if flash_message:
                    flash(msg, "error")
                
                if json_response:
                    return jsonify(success=False, message=msg), status_code
                
                if not flash_message and not json_response:
                    raise HTTPException(response=msg, code=status_code)
                    
                return cast(T, None)
                
            except SQLAlchemyError as e:
                db.session.rollback()
                msg = error_messages.get(SQLAlchemyError, DB_ERROR_MESSAGES[SQLAlchemyError])
                
                if log_error:
                    logging.error(f"SQLAlchemyError in {func.__name__}: {str(e)}")
                    logging.debug(traceback.format_exc())
                
                if flash_message:
                    flash(msg, "error")
                
                if json_response:
                    return jsonify(success=False, message=msg), status_code
                
                if not flash_message and not json_response:
                    raise HTTPException(response=msg, code=status_code)
                    
                return cast(T, None)
                
            except Exception as e:
                db.session.rollback()
                msg = error_messages.get(Exception, DB_ERROR_MESSAGES[Exception])
                
                if log_error:
                    logging.error(f"Unexpected exception in {func.__name__}: {str(e)}")
                    logging.debug(traceback.format_exc())
                
                if flash_message:
                    flash(msg, "error")
                
                if json_response:
                    return jsonify(success=False, message=msg), status_code
                
                if not flash_message and not json_response:
                    raise HTTPException(response=msg, code=status_code)
                    
                return cast(T, None)
        
        return wrapper
    
    # This allows the decorator to be used with or without arguments
    if func is not None:
        return decorator(func)
    return decorator


class DBErrorHandler:
    """
    Context manager for handling database errors.
    
    Example:
        with DBErrorHandler(flash_message=True, json_response=False):
            # Database operations
            db.session.add(new_record)
            db.session.commit()
    """
    
    def __init__(
        self, 
        json_response: bool = False,
        flash_message: bool = True,
        log_error: bool = True,
        custom_error_messages: Optional[Dict[Any, str]] = None,
        status_code: int = 500
    ):
        self.json_response = json_response
        self.flash_message = flash_message
        self.log_error = log_error
        self.status_code = status_code
        self.error_messages = DB_ERROR_MESSAGES.copy()
        if custom_error_messages:
            self.error_messages.update(custom_error_messages)
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            return False  # No exception occurred
            
        if issubclass(exc_type, HTTPException):
            return False  # Let HTTP exceptions pass through
            
        # Handle database errors
        if issubclass(exc_type, IntegrityError):
            db.session.rollback()
            msg = self.error_messages.get(IntegrityError, DB_ERROR_MESSAGES[IntegrityError])
            
            if self.log_error:
                logging.error(f"IntegrityError: {str(exc_val)}")
                logging.debug(traceback.format_exc())
            
            if self.flash_message:
                flash(msg, "error")
            
            if self.json_response:
                return jsonify(success=False, message=msg), self.status_code
            
            return True  # Exception handled
            
        elif issubclass(exc_type, OperationalError):
            db.session.rollback()
            msg = self.error_messages.get(OperationalError, DB_ERROR_MESSAGES[OperationalError])
            
            if self.log_error:
                logging.error(f"OperationalError: {str(exc_val)}")
                logging.debug(traceback.format_exc())
            
            if self.flash_message:
                flash(msg, "error")
            
            if self.json_response:
                return jsonify(success=False, message=msg), self.status_code
            
            return True
            
        elif issubclass(exc_type, SQLAlchemyError):
            db.session.rollback()
            msg = self.error_messages.get(SQLAlchemyError, DB_ERROR_MESSAGES[SQLAlchemyError])
            
            if self.log_error:
                logging.error(f"SQLAlchemyError: {str(exc_val)}")
                logging.debug(traceback.format_exc())
            
            if self.flash_message:
                flash(msg, "error")
            
            if self.json_response:
                return jsonify(success=False, message=msg), self.status_code
            
            return True
            
        elif issubclass(exc_type, Exception):
            db.session.rollback()
            msg = self.error_messages.get(Exception, DB_ERROR_MESSAGES[Exception])
            
            if self.log_error:
                logging.error(f"Unexpected exception: {str(exc_val)}")
                logging.debug(traceback.format_exc())
            
            if self.flash_message:
                flash(msg, "error")
            
            if self.json_response:
                return jsonify(success=False, message=msg), self.status_code
            
            return True
            
        return False  # Re-raise exception if not handled
