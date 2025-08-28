# Reusable Calendar Component Documentation

## Overview

The reusable calendar component is a flexible, configurable calendar widget that can be used across different pages in your Flask application. It supports date highlighting, month navigation, date selection, and custom styling.

## Components

### 1. Template Macro (`partials/calendar_component.html`)
- Generates the HTML structure for the calendar
- Accepts configuration parameters
- Embeds configuration as JSON for JavaScript access

### 2. JavaScript Class (`static/js/reusable-calendar.js`)
- `ReusableCalendar` class handles all calendar functionality
- Factory functions for easy calendar creation
- Global registry for calendar instance management

### 3. CSS Styles (`static/css/reusable-calendar.css`)
- Base calendar styling
- Responsive design
- Multiple highlight types (pending, confirmed, selected, today)
- Size variants (compact, normal, large)

## Usage

### Basic Setup

1. **Import the macro in your template:**
```jinja2
{% from "partials/calendar_component.html" import calendar_widget %}
```

2. **Include the calendar in your template:**
```jinja2
{{ calendar_widget('my-calendar-id') }}
```

3. **Include CSS and JavaScript:**
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/reusable-calendar.css') }}">
<script src="{{ url_for('static', filename='js/reusable-calendar.js') }}"></script>
```

4. **Initialize the calendar in JavaScript:**
```javascript
const calendar = createCalendar('my-calendar-id', {
    onDateClick: (year, month, day, dateStr) => {
        console.log('Date clicked:', dateStr);
    },
    onMonthChange: (year, month) => {
        console.log('Month changed:', year, month);
    }
});
```

### Advanced Configuration

#### Template Configuration
```jinja2
{{ calendar_widget(
    'admin-calendar',
    config={
        'selectable': true,
        'highlight_today': true,
        'allow_other_month_nav': true,
        'autoSelectToday': false,
        'cell_classes': {
            'pending': 'calendar-cell-pending',
            'confirmed': 'calendar-cell-confirmed',
            'selected': 'calendar-cell-selected'
        }
    },
    show_navigation=true,
    show_loading=true,
    footer_text='Custom footer text here'
) }}
```

#### JavaScript Configuration
```javascript
const calendar = createCalendar('my-calendar', {
    // Event callbacks
    onDateClick: handleDateClick,
    onMonthChange: handleMonthChange,
    onDateSelect: handleDateSelect,
    
    // Runtime options (override template config)
    autoSelectToday: true
});
```

### Highlighting Dates

#### Set Multiple Highlighted Dates
```javascript
calendar.setHighlightedDates({
    '2025-08-15': { type: 'pending', data: { booking_id: 123 } },
    '2025-08-20': { type: 'confirmed', data: { booking_id: 456 } },
    '2025-08-25': { type: 'pending', data: { booking_id: 789 } }
});
```

#### Add Single Highlighted Date
```javascript
calendar.addHighlightedDate('2025-08-30', { 
    type: 'confirmed', 
    data: { booking_id: 999, walker_name: 'John' } 
});
```

#### Remove Highlighted Date
```javascript
calendar.removeHighlightedDate('2025-08-15');
```

### Navigation

#### Programmatic Navigation
```javascript
// Go to specific month
calendar.goToMonth(2025, 12);

// Navigate by offset
calendar.navigateMonth(1);  // Next month
calendar.navigateMonth(-1); // Previous month
```

#### Date Selection
```javascript
// Select specific date
calendar.selectDate(2025, 8, 15);

// Get current selection
const selected = calendar.selectedDate; // {year, month, day}
```

### Loading States

```javascript
// Show loading
calendar.showLoading();

// Hide loading
calendar.hideLoading();
```

## Event Callbacks

### onDateClick(year, month, day, dateStr)
Triggered when a user clicks on a calendar date.
- `year`: 4-digit year
- `month`: 1-based month (1-12)
- `day`: Day of month (1-31)
- `dateStr`: Formatted date string 'YYYY-MM-DD'

### onMonthChange(year, month)
Triggered when the calendar navigates to a different month.
- `year`: 4-digit year
- `month`: 1-based month (1-12)

### onDateSelect(year, month, day)
Triggered when a date is selected (includes programmatic selection).
- `year`: 4-digit year
- `month`: 1-based month (1-12)
- `day`: Day of month (1-31)

## CSS Classes

### Highlight Types
- `.calendar-cell-pending` - Yellow background for pending items
- `.calendar-cell-confirmed` - Green background for confirmed items
- `.calendar-cell-selected` - Blue background for selected date
- `.calendar-cell-today` - Light blue background for today
- `.calendar-cell-multiple` - Gradient background for multiple types

### Size Variants
- `.calendar-compact` - Smaller calendar cells
- `.calendar-large` - Larger calendar cells

### States
- `.calendar-cell-other-month` - Grayed out dates from other months
- `.calendar-cell-disabled` - Disabled dates (not clickable)

## Examples

### Admin Calendar (Current Implementation)
```javascript
const adminCalendar = createCalendar('admin-calendar', {
    onDateClick: (year, month, day, dateStr) => {
        loadBookingsForDate(dateStr);
    },
    onMonthChange: async (year, month) => {
        const data = await fetchCalendarData(year, month);
        const highlightedDates = {};
        data.pending_dates.forEach(day => {
            const dateStr = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
            highlightedDates[dateStr] = { type: 'pending' };
        });
        adminCalendar.setHighlightedDates(highlightedDates);
    }
});
```

### Client Calendar
```javascript
const clientCalendar = createCalendar('client-calendar', {
    onDateClick: (year, month, day, dateStr) => {
        showBookingDetails(dateStr);
    },
    onMonthChange: async (year, month) => {
        const bookings = await fetchClientBookings(year, month);
        const highlightedDates = {};
        bookings.forEach(booking => {
            highlightedDates[booking.date] = { 
                type: booking.status, // 'pending' or 'confirmed'
                data: booking 
            };
        });
        clientCalendar.setHighlightedDates(highlightedDates);
    }
});
```

## Browser Support

- Modern browsers (Chrome, Firefox, Safari, Edge)
- ES6+ features used (requires transpilation for older browsers)
- CSS Grid support required

## Performance Notes

- Calendar renders efficiently with minimal DOM manipulation
- Event delegation used for click handlers
- Loading states prevent multiple simultaneous API calls
- Calendar instances are cached in global registry

## Migration from Old Calendar

### Before (admin.html)
```javascript
// 300+ lines of custom calendar code
function renderCalendar(year, month) { /* ... */ }
function createDateCell(day, year, month, isOtherMonth) { /* ... */ }
// ... many more functions
```

### After (admin.html)
```javascript
// 20 lines using reusable component
const adminCalendar = createCalendar('admin-calendar', {
    onDateClick: handleDateClick,
    onMonthChange: handleMonthChange
});
```

### Benefits of Migration
- **90% less code** in templates
- **Consistent behavior** across all calendars
- **Easier maintenance** - changes in one place
- **Better testing** - isolated calendar logic
- **Flexible configuration** - different use cases supported
