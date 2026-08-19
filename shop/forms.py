from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import CustomUser, ContactMessage, Course


class RegistrationForm(UserCreationForm):
    username = forms.CharField(max_length=150, required=True, help_text="Required.")
    name = forms.CharField(max_length=150, required=True, help_text="Required.")
    email = forms.EmailField(required=True, help_text="Required.")
    phone_number = forms.CharField(max_length=20, required=True, help_text="Required.")
    age = forms.IntegerField(required=False, help_text="Optional.")

    class Meta:
        model = CustomUser
        fields = [
            "username",
            "name",
            "email",
            "phone_number",
            "age",
            "password1",
            "password2",
        ]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if CustomUser.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("This username is already in use.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email address is already in use.")
        return email

    def clean_phone_number(self):
        phone_number = " ".join(self.cleaned_data["phone_number"].split())
        if CustomUser.objects.filter(phone_number__iexact=phone_number).exists():
            raise forms.ValidationError("This phone number is already in use.")
        return phone_number

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data.get('username')
        user.name = self.cleaned_data.get('name')
        user.email = self.cleaned_data.get('email')
        user.phone_number = self.cleaned_data.get('phone_number')
        user.age = self.cleaned_data.get('age')
        if commit:
            user.save()
        return user


class ProfileImageForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ["profile_image"]
        widgets = {
            "profile_image": forms.FileInput(attrs={"accept": "image/*"}),
        }


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = [
            "title",
            "description",
            "thumbnail",
            "video",
            "instructor",
            "duration",
            "level",
            "is_active",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Course title"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "Course description"}),
            "thumbnail": forms.FileInput(attrs={"accept": "image/*"}),
            "video": forms.FileInput(attrs={"accept": "video/*"}),
            "instructor": forms.TextInput(attrs={"placeholder": "Instructor name"}),
            "duration": forms.TextInput(attrs={"placeholder": "e.g. 4 weeks"}),
            "level": forms.Select(),
            "is_active": forms.CheckboxInput(),
        }


class ContactMessageForm(forms.ModelForm):
    class Meta:
        model = ContactMessage
        fields = ["message", "image", "video"]
        widgets = {
            "message": forms.Textarea(attrs={
                "placeholder": "Type your message...",
                "rows": 3,
            }),
            "image": forms.FileInput(attrs={"accept": "image/*"}),
            "video": forms.FileInput(attrs={"accept": "video/*"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make all fields optional
        self.fields["message"].required = False
        self.fields["image"].required = False
        self.fields["video"].required = False

    def clean(self):
        cleaned_data = super().clean()
        message = cleaned_data.get("message", "").strip() if cleaned_data.get("message") else ""
        image = cleaned_data.get("image")
        video = cleaned_data.get("video")

        # At least one field must be provided
        if not message and not image and not video:
            raise forms.ValidationError("Please provide a message, image, or video.")

        # Update message to stripped version
        if message:
            cleaned_data["message"] = message
        else:
            cleaned_data["message"] = ""

        return cleaned_data