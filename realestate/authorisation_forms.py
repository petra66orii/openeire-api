from django import forms
from django.utils import timezone

from .models import PropertyScopeCheck


CAPACITIES = [("owner", "Property owner"), ("agent", "Estate agent / auctioneer"), ("representative", "Authorised representative"), ("other", "Other")]
PREFERENCES = [("none", "No specific requirements - I am happy for OpenÉire to use its professional judgement within the agreed package/scope"), ("specific", "Yes - I have specific must-capture requirements")]


class AuthorisationForm(forms.Form):
    full_name = forms.CharField(max_length=255, label="Full name")
    email = forms.EmailField()
    capacity = forms.ChoiceField(choices=CAPACITIES)
    capacity_other = forms.CharField(max_length=200, required=False, label="If Other, explain your capacity")
    owner_name = forms.CharField(max_length=255, required=False, label="Property owner name, if different")
    business_name = forms.CharField(max_length=255, required=False, label="Agency / business name")
    restrictions_choice = forms.ChoiceField(choices=[("none", "None"), ("specific", "Restrictions apply")], widget=forms.RadioSelect, label="Areas OpenÉire must not enter, access or photograph")
    restrictions = forms.CharField(required=False, max_length=10000, widget=forms.Textarea(attrs={"rows": 3}), label="Restricted areas / locations")
    hazards_choice = forms.ChoiceField(choices=[("none", "No known hazards to disclose"), ("specific", "Known hazards / precautions")], widget=forms.RadioSelect)
    hazards = forms.CharField(required=False, max_length=10000, widget=forms.Textarea(attrs={"rows": 3}), label="Known hazards / precautions", help_text="For example: unstable structures, derelict buildings, construction, electricity, machinery, animals, electric fencing, water, unstable ground, holes, drains, trenches or hazardous substances.")
    capture_choice = forms.ChoiceField(choices=PREFERENCES, widget=forms.RadioSelect, label="Do you have any specific must-capture features or requirements?")
    must_capture = forms.CharField(required=False, max_length=15000, widget=forms.Textarea(attrs={"rows": 4}), label="Essential / must-capture features", help_text="Leave blank or enter N/A if none. Otherwise identify relevant features and locations: rooms, views, boundaries, fencing, access, structures, parcels, forestry, bog, lakes, roads or angles.")
    video_choice = forms.ChoiceField(required=False, choices=[("", "Not provided / N/A"), ("none", "No specific video requirements - I am happy for OpenÉire to use its professional judgement within the agreed scope"), ("specific", "Yes - I have specific video requirements")], widget=forms.RadioSelect, label="Specific requirements for the property film / social-media video (if included)")
    video_requirements = forms.CharField(required=False, max_length=10000, widget=forms.Textarea(attrs={"rows": 3}), help_text="Optional. This does not add video to the booking.")
    authority_confirmed = forms.BooleanField(label="I own the specified Property or have the owner's authority to permit this work, and have read and understood the complete Authorisation. To the best of my knowledge the information I provide is accurate.")
    typed_signature = forms.CharField(max_length=255, label="Type your full name to accept electronically")

    def __init__(self, *args, video_included=False, handwritten=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.handwritten = handwritten
        if not video_included:
            self.fields.pop("video_choice")
            self.fields.pop("video_requirements")
        if handwritten:
            self.fields.pop("typed_signature")
            self.fields["authority_confirmed"].label = "The returned handwritten document confirms authority and acceptance; these fields faithfully transcribe it."
            self.fields["received_on"] = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}), label="Date signed copy received")

    def clean(self):
        data = super().clean()
        if data.get("capacity") == "other" and not data.get("capacity_other"):
            self.add_error("capacity_other", "Please explain your capacity.")
        for choice, field in [("restrictions_choice", "restrictions"), ("hazards_choice", "hazards"), ("capture_choice", "must_capture"), ("video_choice", "video_requirements")]:
            text = data.get(field, "")
            if data.get(choice) == "specific" and text.lower() in ("", "n/a", "na"):
                self.add_error(field, "Describe your requirements, or select the no-specific-requirements option.")
            elif data.get(choice) in ("none", "", None) and text.lower() not in ("", "n/a", "na"):
                self.add_error(field, "Select the specific option to include these details.")
        if not self.handwritten and data.get("typed_signature", "").casefold() != data.get("full_name", "").casefold():
            self.add_error("typed_signature", "Type the same full name as above.")
        if data.get("received_on") and data["received_on"] > timezone.localdate():
            self.add_error("received_on", "The received date cannot be in the future.")
        return data


class IssueAuthorisationForm(forms.Form):
    location = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), max_length=10000, label="Property / locations covered", help_text="List only the locations this person can authorise. Create separate authorisations for different owners or representatives.")
    recipient_email = forms.EmailField(label="Authorising person's email (may differ from payer)")


class ScopeCheckForm(forms.ModelForm):
    class Meta:
        model = PropertyScopeCheck
        fields = ("location", "person_confirming", "capacity", "outcome", "all_agreed_completed", "outstanding_capture", "notes")
        widgets = {"location": forms.Textarea(attrs={"rows": 2}), "outstanding_capture": forms.Textarea(attrs={"rows": 3}), "notes": forms.Textarea(attrs={"rows": 3})}
