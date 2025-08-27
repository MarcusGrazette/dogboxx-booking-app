"""
API routes.

This module defines API routes for the application, providing JSON endpoints for
AJAX operations and potential external integrations.
"""

from flask import jsonify, request
from flask_login import login_required, current_user
from app.models import User, Client, Dog, Booking, Walker
from app import db
from app.utils.db_error_handler import handle_db_errors
from datetime import datetime
import logging
import traceback

from app.blueprints.api import api_bp


@api_bp.route("/bookings", methods=["GET"])
@login_required
def get_bookings():
    """Get bookings for the current user based on role."""
    try:
        # Get query parameters
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        status = request.args.get('status')
        
        # Base query - filter by relevant user relationship
        query = None
        
        if current_user.is_admin:
            # Admins see all bookings
            query = Booking.query
        elif hasattr(current_user, 'walker') and current_user.walker:
            # Walkers see their assigned bookings
            query = Booking.query.filter_by(walker_id=current_user.walker.id)
        elif hasattr(current_user, 'client') and current_user.client:
            # Clients see their own bookings
            query = Booking.query.join(Dog).filter(Dog.client_id == current_user.client.id)
        else:
            return jsonify(success=False, message="User role not recognized"), 403
            
        # Apply date filters if provided
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query = query.filter(Booking.date >= start_date)
            except ValueError:
                return jsonify(success=False, message="Invalid start_date format"), 400
                
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                query = query.filter(Booking.date <= end_date)
            except ValueError:
                return jsonify(success=False, message="Invalid end_date format"), 400
                
        # Apply status filter if provided
        if status:
            query = query.filter(Booking.status == status)
            
        # Execute query and format results
        bookings = query.all()
        result = []
        
        for booking in bookings:
            dog = booking.dog
            walker = booking.walker
            
            booking_data = {
                "id": booking.id,
                "date": booking.date.strftime('%Y-%m-%d'),
                "time_slot": booking.time_slot,
                "status": booking.status,
                "notes": booking.notes,
                "dog": {
                    "id": dog.id,
                    "name": dog.name,
                    "breed": dog.breed
                }
            }
            
            if walker:
                booking_data["walker"] = {
                    "id": walker.id,
                    "name": f"{walker.user.first_name} {walker.user.last_name}"
                }
                
            result.append(booking_data)
            
        return jsonify(success=True, bookings=result)
        
    except Exception as e:
        logging.error(f"Error in get_bookings: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify(success=False, message="An error occurred"), 500


@api_bp.route("/user", methods=["GET"])
@login_required
def get_user():
    """Get current user information including role-specific details."""
    try:
        user_data = {
            "id": current_user.id,
            "email": current_user.email,
            "first_name": current_user.first_name,
            "last_name": current_user.last_name,
            "is_admin": current_user.is_admin,
            "onboarded": current_user.onboarded
        }
        
        # Add role-specific information
        if hasattr(current_user, 'walker') and current_user.walker:
            user_data["role"] = "walker"
            user_data["walker_id"] = current_user.walker.id
            user_data["walker_info"] = {
                "bio": current_user.walker.bio,
                "experience_years": current_user.walker.experience_years,
                "available_days": current_user.walker.available_days,
                "available_times": current_user.walker.available_times
            }
        elif hasattr(current_user, 'client') and current_user.client:
            user_data["role"] = "client"
            user_data["client_id"] = current_user.client.id
            
            # Get dogs associated with this client
            dogs = []
            for dog in current_user.client.dogs:
                dogs.append({
                    "id": dog.id,
                    "name": dog.name,
                    "breed": dog.breed,
                    "age": dog.age,
                    "size": dog.size,
                    "notes": dog.notes
                })
            user_data["dogs"] = dogs
        elif current_user.is_admin:
            user_data["role"] = "admin"
        else:
            user_data["role"] = "unassigned"
        
        return jsonify(success=True, user=user_data)
        
    except Exception as e:
        logging.error(f"Error in get_user: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify(success=False, message="An error occurred"), 500


@api_bp.route("/bookings/<int:booking_id>", methods=["GET"])
@login_required
def get_booking_details(booking_id):
    """Get detailed information about a specific booking."""
    try:
        booking = Booking.query.get(booking_id)
        
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404
            
        # Check permissions
        if not current_user.is_admin:
            if hasattr(current_user, 'walker') and current_user.walker:
                if booking.walker_id != current_user.walker.id:
                    return jsonify(success=False, message="Access denied"), 403
            elif hasattr(current_user, 'client') and current_user.client:
                if booking.dog.client_id != current_user.client.id:
                    return jsonify(success=False, message="Access denied"), 403
            else:
                return jsonify(success=False, message="Access denied"), 403
                
        # Get related objects
        dog = booking.dog
        client = dog.client
        walker = booking.walker
        
        # Format response
        booking_data = {
            "id": booking.id,
            "date": booking.date.strftime('%Y-%m-%d'),
            "time_slot": booking.time_slot,
            "status": booking.status,
            "notes": booking.notes,
            "dog": {
                "id": dog.id,
                "name": dog.name,
                "breed": dog.breed,
                "age": dog.age,
                "size": dog.size,
                "notes": dog.notes
            },
            "client": {
                "id": client.id,
                "name": f"{client.user.first_name} {client.user.last_name}",
                "phone": client.phone,
                "address": client.address
            }
        }
        
        if walker:
            booking_data["walker"] = {
                "id": walker.id,
                "name": f"{walker.user.first_name} {walker.user.last_name}",
                "phone": walker.phone
            }
            
        return jsonify(success=True, booking=booking_data)
        
    except Exception as e:
        logging.error(f"Error in get_booking_details: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify(success=False, message="An error occurred"), 500


@api_bp.route("/bookings/<int:booking_id>/status", methods=["PUT"])
@login_required
def update_booking_status(booking_id):
    """Update the status of a booking."""
    try:
        booking = Booking.query.get(booking_id)
        
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404
            
        # Check permissions
        if not current_user.is_admin:
            if hasattr(current_user, 'walker') and current_user.walker:
                if booking.walker_id != current_user.walker.id:
                    return jsonify(success=False, message="Access denied"), 403
            else:
                return jsonify(success=False, message="Access denied"), 403
        
        # Get new status from request
        data = request.get_json()
        if not data or 'status' not in data:
            return jsonify(success=False, message="Status field required"), 400
            
        new_status = data['status']
        valid_statuses = ['scheduled', 'in_progress', 'completed', 'cancelled']
        
        if new_status not in valid_statuses:
            return jsonify(success=False, message=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"), 400
            
        # Update the booking
        with handle_db_errors():
            booking.status = new_status
            db.session.commit()
            
        return jsonify(success=True, message="Booking status updated", status=new_status)
        
    except Exception as e:
        logging.error(f"Error in update_booking_status: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify(success=False, message="An error occurred"), 500


@api_bp.route("/walkers", methods=["GET"])
@login_required
def get_walkers():
    """Get list of all walkers with availability information."""
    try:
        # Base query for walkers
        walkers = Walker.query.all()
        result = []
        
        for walker in walkers:
            walker_data = {
                "id": walker.id,
                "name": f"{walker.user.first_name} {walker.user.last_name}",
                "bio": walker.bio,
                "experience_years": walker.experience_years,
                "available_days": walker.available_days,
                "available_times": walker.available_times,
                "phone": walker.phone
            }
            result.append(walker_data)
            
        return jsonify(success=True, walkers=result)
        
    except Exception as e:
        logging.error(f"Error in get_walkers: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify(success=False, message="An error occurred"), 500
