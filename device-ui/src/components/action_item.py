"""
Action Item Component

Widget for displaying action items with checkboxes.
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from config import COLORS, FONT_SIZES


class ActionItemWidget(BoxLayout):
    """
    Action item widget.
    
    Shows:
    - Checkbox (completed state)
    - Task text
    - Assignee (if present)
    - Due date (if present)
    """
    
    def __init__(self, action_item: dict, **kwargs):
        self.action_item = action_item
        
        kwargs.setdefault('orientation', 'horizontal')
        kwargs.setdefault('size_hint_y', None)
        kwargs.setdefault('spacing', 8)
        
        super().__init__(**kwargs)
        
        # Checkbox
        checkbox = CheckBox(
            active=action_item.get('completed', False),
            size_hint=(None, 1),
            width=40
        )
        checkbox.bind(active=self.on_checkbox_toggled)
        self.add_widget(checkbox)
        
        # Text container
        text_container = BoxLayout(
            orientation='vertical',
            size_hint=(1, None),
            spacing=2
        )
        
        # Task text
        task = action_item.get('task', '')
        task_label = Label(
            text=task,
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_900'],
            halign='left',
            valign='top',
            size_hint=(1, None)
        )
        task_label.bind(
            texture_size=task_label.setter('size')
        )
        task_label.bind(size=task_label.setter('text_size'))
        text_container.add_widget(task_label)
        
        # Metadata (assignee, due date)
        meta_parts = []
        if action_item.get('assignee'):
            meta_parts.append(action_item['assignee'])
        if action_item.get('due_date'):
            meta_parts.append(action_item['due_date'])
        
        if meta_parts:
            meta_label = Label(
                text='  |  '.join(meta_parts),
                font_size=FONT_SIZES['tiny'],
                color=COLORS['gray_500'],
                halign='left',
                valign='top',
                size_hint=(1, None),
                height=20
            )
            meta_label.bind(size=meta_label.setter('text_size'))
            text_container.add_widget(meta_label)
        
        # Calculate total height
        text_container.height = task_label.height + (20 if meta_parts else 0) + 4
        self.height = text_container.height + 10
        
        self.add_widget(text_container)
    
    def on_checkbox_toggled(self, checkbox, value):
        """Handle checkbox toggle"""
        self.action_item['completed'] = value
        # Could call backend API to update completion status
