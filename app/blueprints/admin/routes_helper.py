"""
Admin routes helper functions.

This module contains helper functions for admin routes.
"""

import logging
import traceback

def generate_bookings_html(pending_bookings, assigned_bookings, walkers, walker_capacity):
    """
    Generate HTML for the bookings drag and drop interface.
    
    Args:
        pending_bookings: List of pending bookings
        assigned_bookings: List of assigned bookings
        walkers: List of walker objects
        walker_capacity: Dictionary mapping walker IDs to their capacity
        
    Returns:
        HTML string for the drag and drop interface
    """
    html = '''
    <div class="row g-3" id="drag-drop-container">
        <!-- Pending Bookings Column -->
        <div class="col-md-3">
            <div class="card h-100">
                <div class="card-header bg-warning text-dark">
                    <h6 class="mb-0"><i class="bi bi-hourglass"></i> Pending</h6>
                </div>
                <div class="card-body p-2">
                    <!-- Morning Pending -->
                    <div class="drop-zone pending-zone" data-slot="Morning" data-walker-id="">
                        <h6 class="text-muted mb-2">Morning</h6>
                        <div class="booking-cards">
    '''
    
    # Add morning pending bookings
    morning_pending = [b for b in pending_bookings if b.slot == 'Morning']
    for booking in morning_pending:
        pic_src = f"/static/images/{booking.dog_pic}" if booking.dog_pic else "/static/images/default-dog.png"
        html += f'''
            <div class="card booking-card draggable bg-light border-dark" 
                draggable="true" 
                data-booking-id="{booking.id}"
                data-current-slot="{booking.slot}"
                data-current-walker-id="{booking.walker_id or ''}"
                data-dog-name="{booking.dog_name}"
                data-dog-pic="{booking.dog_pic or ''}">
                <div class="d-flex align-items-center gap-2 p-2">
                    <div style="width: 30px; height: 30px; overflow: hidden;" class="rounded-circle flex-shrink-0">
                        <img src="{pic_src}" class="img-fluid" alt="{booking.dog_name}" 
                            style="width: 100%; height: 100%; object-fit: cover;"
                            onerror="this.onerror=null; this.src='/static/images/default-dog.png'">
                    </div>
                    <div>
                        <small>{booking.dog_name}</small>
                    </div>
                </div>
            </div>
        '''
    
    html += '''
                        </div>
                    </div>
                    
                    <hr>
                    
                    <!-- Afternoon Pending -->
                    <div class="drop-zone pending-zone" data-slot="Afternoon" data-walker-id="">
                        <h6 class="text-muted mb-2">Afternoon</h6>
                        <div class="booking-cards">
    '''
    
    # Add afternoon pending bookings
    afternoon_pending = [b for b in pending_bookings if b.slot == 'Afternoon']
    for booking in afternoon_pending:
        pic_src = f"/static/images/{booking.dog_pic}" if booking.dog_pic else "/static/images/default-dog.png"
        html += f'''
            <div class="card booking-card draggable bg-light border-dark" 
                draggable="true" 
                data-booking-id="{booking.id}"
                data-current-slot="{booking.slot}"
                data-current-walker-id="{booking.walker_id or ''}"
                data-dog-name="{booking.dog_name}"
                data-dog-pic="{booking.dog_pic or ''}">
                <div class="d-flex align-items-center gap-2 p-2">
                    <div style="width: 30px; height: 30px; overflow: hidden;" class="rounded-circle flex-shrink-0">
                        <img src="{pic_src}" class="img-fluid" alt="{booking.dog_name}" 
                            style="width: 100%; height: 100%; object-fit: cover;"
                            onerror="this.onerror=null; this.src='/static/images/default-dog.png'">
                    </div>
                    <div>
                        <small>{booking.dog_name}</small>
                    </div>
                </div>
            </div>
        '''
    
    html += '''
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Walkers Column -->
        <div class="col-md-9">
            <div class="row g-3">
    '''
    
    # Add walker columns
    for walker in walkers:
        html += f'''
            <div class="col-md-4 mb-3">
                <div class="card h-100">
                    <div class="card-header bg-info bg-opacity-50">
                        <h6 class="mb-0">{walker.user.first_name if hasattr(walker, 'user') else 'Walker'}</h6>
                    </div>
                    <div class="card-body p-2">
                        <!-- Morning Slot -->
                        <div class="drop-zone walker-zone {'' if walker_capacity[walker.id]['Morning'] < 6 else 'bg-danger bg-opacity-25'}" 
                            data-slot="Morning" 
                            data-walker-id="{walker.id}">
                            <h6 class="text-muted mb-2">Morning ({walker_capacity[walker.id]['Morning']}/6)</h6>
                            <div class="booking-cards">
        '''
        
        # Add morning assigned bookings for this walker
        morning_assigned = [b for b in assigned_bookings if b.walker_id == walker.id and b.slot == 'Morning']
        for booking in morning_assigned:
            pic_src = f"/static/images/{booking.dog_pic}" if booking.dog_pic else "/static/images/default-dog.png"
            html += f'''
                <div class="card booking-card draggable bg-light border-success" 
                    draggable="true" 
                    data-booking-id="{booking.id}"
                    data-current-slot="{booking.slot}"
                    data-current-walker-id="{booking.walker_id}"
                    data-dog-name="{booking.dog_name}"
                    data-dog-pic="{booking.dog_pic or ''}">
                    <div class="d-flex align-items-center gap-2 p-2">
                        <div style="width: 30px; height: 30px; overflow: hidden;" class="rounded-circle flex-shrink-0">
                            <img src="{pic_src}" class="img-fluid" alt="{booking.dog_name}" 
                                style="width: 100%; height: 100%; object-fit: cover;"
                                onerror="this.onerror=null; this.src='/static/images/default-dog.png'">
                        </div>
                        <div>
                            <small>{booking.dog_name}</small>
                        </div>
                    </div>
                </div>
            '''
        
        html += '''
                            </div>
                        </div>
                        
                        <hr>
                        
                        <!-- Afternoon Slot -->
        '''
        
        html += f'''
                        <div class="drop-zone walker-zone {'' if walker_capacity[walker.id]['Afternoon'] < 6 else 'bg-danger bg-opacity-25'}" 
                            data-slot="Afternoon" 
                            data-walker-id="{walker.id}">
                            <h6 class="text-muted mb-2">Afternoon ({walker_capacity[walker.id]['Afternoon']}/6)</h6>
                            <div class="booking-cards">
        '''
        
        # Add afternoon assigned bookings for this walker
        afternoon_assigned = [b for b in assigned_bookings if b.walker_id == walker.id and b.slot == 'Afternoon']
        for booking in afternoon_assigned:
            pic_src = f"/static/images/{booking.dog_pic}" if booking.dog_pic else "/static/images/default-dog.png"
            html += f'''
                <div class="card booking-card draggable bg-light border-success" 
                    draggable="true" 
                    data-booking-id="{booking.id}"
                    data-current-slot="{booking.slot}"
                    data-current-walker-id="{booking.walker_id}"
                    data-dog-name="{booking.dog_name}"
                    data-dog-pic="{booking.dog_pic or ''}">
                    <div class="d-flex align-items-center gap-2 p-2">
                        <div style="width: 30px; height: 30px; overflow: hidden;" class="rounded-circle flex-shrink-0">
                            <img src="{pic_src}" class="img-fluid" alt="{booking.dog_name}" 
                                style="width: 100%; height: 100%; object-fit: cover;"
                                onerror="this.onerror=null; this.src='/static/images/default-dog.png'">
                        </div>
                        <div>
                            <small>{booking.dog_name}</small>
                        </div>
                    </div>
                </div>
            '''
        
        html += '''
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        '''
    
    # Close walker column and row
    html += '''
            </div>
        </div>
    </div>
    '''
    
    return html


def handle_admin_bookings_by_date_error(e):
    """
    Handle errors in the admin_bookings_by_date route.
    
    Args:
        e: The exception that occurred
        
    Returns:
        Tuple of (error message, status code)
    """
    logging.error(f"Error in admin_bookings_by_date: {str(e)}")
    logging.error(traceback.format_exc())
    return "Server error", 500
